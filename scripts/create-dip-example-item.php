<?php
/**
 * Create an Omeka item with a fixture DIP (runs inside the Omeka container).
 *
 * Usage: php scripts/create-dip-example-item.php [/path/to/package.tar]
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$tarPath = $argv[1] ?? '/fixtures/dips/built/sample-game.tar';
$itemTitleOverride = $argv[2] ?? null;
if (!is_readable($tarPath)) {
    fwrite(STDERR, "DIP not found or unreadable: {$tarPath}\n");
    exit(1);
}

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$entityManager = $services->get('Omeka\EntityManager');

$admin = $entityManager->getRepository(User::class)->findOneBy(['email' => 'admin@example.com']);
if (!$admin) {
    fwrite(STDERR, "Admin user admin@example.com not found.\n");
    exit(1);
}
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$api = $services->get('Omeka\ApiManager');

$settingsPath = '/config/settings.yaml';
$yaml = is_readable($settingsPath) ? file_get_contents($settingsPath) : '';
preg_match('/^\s*site_slug:\s*(\S+)/m', $yaml, $slugMatch);
$siteSlug = $slugMatch[1] ?? 'hitsave-test';
$sites = $api->search('sites', ['slug' => $siteSlug])->getContent();
if (!$sites) {
    fwrite(STDERR, "No site with slug {$siteSlug}; run scripts/ensure-omeka-test-site.php first.\n");
    exit(1);
}
$siteId = $sites[0]->id();

$titleProperty = $api->search('properties', ['term' => 'dcterms:title'])->getContent()[0];

try {
    $item = $api->create('items', [
        'dcterms:title' => [[
            'property_id' => $titleProperty->id(),
            'type' => 'literal',
            '@value' => $itemTitleOverride ?: 'Sample Game Press Kit (DIP example)',
        ]],
        'o:is_public' => true,
        'o:site' => [['o:id' => $siteId]],
    ])->getContent();
} catch (Throwable $e) {
    fwrite(STDERR, 'Item create failed: ' . $e->getMessage() . "\n");
    exit(1);
}
$itemId = $item->id();

$fileData = [
    'file' => [
        'name' => basename($tarPath),
        'type' => 'application/x-tar',
        'tmp_name' => $tarPath,
        'error' => UPLOAD_ERR_OK,
        'size' => filesize($tarPath),
    ],
];

try {
    $media = $api->create('media', [
        'o:ingester' => 'omeka_dip_package',
        'o:item' => ['o:id' => $itemId],
        'o:is_public' => true,
    ], $fileData)->getContent();
} catch (Throwable $e) {
    fwrite(STDERR, 'Media ingest failed: ' . $e->getMessage() . "\n");
    exit(1);
}

/** @var \OmekaDipViewer\Service\MetsParser $metsParser */
$metsParser = $services->get('OmekaDipViewer\Service\MetsParser');
$fileCount = 0;
try {
    $fileCount = count($metsParser->indexTar($tarPath)['files'] ?? []);
} catch (Throwable $e) {
    fwrite(STDERR, 'DIP index check failed: ' . $e->getMessage() . "\n");
}

preg_match('/^\s*public_url:\s*(\S+)/m', $yaml, $urlMatch);
$publicBase = isset($urlMatch[1]) ? rtrim($urlMatch[1], '/') : '';

$itemRead = $api->read('items', $itemId)->getContent();
$mediaRead = $api->read('media', $media->id())->getContent();
$itemArk = firstLiteral($itemRead, 'dcterms:identifier');
$mediaArk = firstLiteral($mediaRead, 'dcterms:identifier');
$expectedItemArk = $itemArk ?: 'ark:/78322/' . $itemId;
$expectedMediaArk = $mediaArk ?: 'ark:/78322/' . $itemId . '/' . $media->id();

$arkItemHttp = null;
$arkMediaHttp = null;
$arkPathSuffix = preg_replace('#^ark:#', '', $itemArk ?: $expectedItemArk);
$arkSiteBase = $publicBase ? "{$publicBase}/s/{$siteSlug}/ark:" : null;
if ($arkSiteBase && $itemArk) {
    $arkItemHttp = probeArkUrl($arkSiteBase . $arkPathSuffix);
}
$mediaArkForProbe = $mediaArk ?: $expectedMediaArk;
if ($arkSiteBase && $mediaArkForProbe) {
    $arkMediaHttp = probeArkUrl($arkSiteBase . preg_replace('#^ark:#', '', $mediaArkForProbe));
}

echo json_encode([
    'item_id' => $itemId,
    'media_id' => $media->id(),
    'dip_files_indexed' => $fileCount,
    'item_ark' => $itemArk,
    'media_ark' => $mediaArk,
    'expected_item_ark' => $expectedItemArk,
    'expected_media_ark' => $expectedMediaArk,
    'ark_item_resolve' => $arkItemHttp,
    'ark_media_resolve' => $arkMediaHttp,
    'admin_item_url' => '/admin/item/' . $itemId . '/show',
    'public_item_url' => $publicBase ? "{$publicBase}/s/{$siteSlug}/item/{$itemId}" : "/s/{$siteSlug}/item/{$itemId}",
    'site_slug' => $siteSlug,
    'tar' => basename($tarPath),
], JSON_PRETTY_PRINT) . "\n";

function firstLiteral($resource, string $term): ?string
{
    if (is_object($resource) && method_exists($resource, 'value')) {
        $values = $resource->value($term, ['type' => 'literal', 'all' => true, 'default' => []]);
        if (is_array($values)) {
            foreach ($values as $value) {
                if (is_object($value) && method_exists($value, 'value')) {
                    $v = (string) $value->value();
                    if ($v !== '') {
                        return $v;
                    }
                }
            }
        }
    }
    return null;
}

/**
 * @return array{http_code: int, effective_url: string|null}|null
 */
function probeArkUrl(string $url): ?array
{
    if (!function_exists('curl_init')) {
        return null;
    }
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_NOBODY => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 15,
    ]);
    curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $effective = curl_getinfo($ch, CURLINFO_EFFECTIVE_URL);
    curl_close($ch);
    return ['http_code' => $code, 'effective_url' => is_string($effective) ? $effective : null];
}
