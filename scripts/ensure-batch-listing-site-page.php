<?php
/**
 * Create/update a public site page listing batch-uploaded items (item set + browse block + nav link).
 *
 * Config: config/omeka-test/settings.yaml → site.batch_listing
 * Usage: php scripts/ensure-batch-listing-site-page.php
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

function yaml_scalar(string $yaml, string $key): ?string
{
    if (preg_match('/^\s*' . preg_quote($key, '/') . ':\s*(.+)$/m', $yaml, $m)) {
        return trim($m[1], " \t\"'");
    }
    return null;
}

$settingsPath = '/config/settings.yaml';
if (!is_readable($settingsPath)) {
    fwrite(STDERR, "Missing {$settingsPath}\n");
    exit(1);
}
$yaml = file_get_contents($settingsPath);
$siteSlug = yaml_scalar($yaml, 'site_slug') ?? 'hitsave-test';

$pageSlug = yaml_scalar($yaml, 'page_slug') ?? 'press-batch';
$pageTitle = yaml_scalar($yaml, 'page_title') ?? 'Press batch uploads';
$navLabel = yaml_scalar($yaml, 'nav_label') ?? $pageTitle;
$itemSetTitle = yaml_scalar($yaml, 'item_set_title') ?? 'Press batch uploads';
$titleSearch = yaml_scalar($yaml, 'item_title_search') ?? '';
$minItemId = (int) (yaml_scalar($yaml, 'min_item_id') ?? '0');
$previewLimit = (int) (yaml_scalar($yaml, 'browse_limit') ?? '100');

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$entityManager = $services->get('Omeka\EntityManager');

$admin = $entityManager->getRepository(User::class)->findOneBy(['email' => 'admin@example.com']);
if (!$admin) {
    fwrite(STDERR, "Admin user not found.\n");
    exit(1);
}
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$api = $services->get('Omeka\ApiManager');

$sites = $api->search('sites', ['slug' => $siteSlug])->getContent();
if (!$sites) {
    fwrite(STDERR, "Site {$siteSlug} not found; run ensure-omeka-test-site.php first.\n");
    exit(1);
}
$site = $sites[0];
$siteId = $site->id();

$titleProperty = $api->search('properties', ['term' => 'dcterms:title'])->getContent()[0];

$items = [];
if ($titleSearch !== '') {
    $items = $api->search('items', ['search' => $titleSearch, 'limit' => 500])->getContent();
}
if (!$items) {
    $itemIds = [];
    $media = $api->search('media', ['renderer' => 'omeka_dip_package', 'limit' => 500])->getContent();
    foreach ($media as $medium) {
        $item = $medium->item();
        if (!$item) {
            continue;
        }
        $itemId = (int) $item->id();
        if ($minItemId > 0 && $itemId < $minItemId) {
            continue;
        }
        $itemIds[$itemId] = $item;
    }
    $items = array_values($itemIds);
}
if (!$items && $minItemId > 0) {
    $candidates = $api->search('items', [
        'limit' => 500,
        'sort_by' => 'id',
        'sort_order' => 'asc',
    ])->getContent();
    foreach ($candidates as $item) {
        if ((int) $item->id() >= $minItemId) {
            $items[] = $item;
        }
    }
}
if (!$items) {
    fwrite(STDERR, "No batch items found (title search or DIP media with min_item_id).\n");
    exit(1);
}

$itemSets = $api->search('item_sets', ['title' => $itemSetTitle])->getContent();
if ($itemSets) {
    $itemSetId = $itemSets[0]->id();
} else {
    $itemSet = $api->create('item_sets', [
        'dcterms:title' => [[
            'property_id' => $titleProperty->id(),
            'type' => 'literal',
            '@value' => $itemSetTitle,
        ]],
        'o:is_public' => true,
    ])->getContent();
    $itemSetId = $itemSet->id();
}

$queryString = 'item_set_id[]=' . rawurlencode((string) $itemSetId);

$existingPageId = null;
foreach ($site->pages() as $sitePage) {
    if ($sitePage->slug() === $pageSlug) {
        $existingPageId = (int) $sitePage->id();
        break;
    }
}

$pagePayload = [
    'o:site' => ['o:id' => $siteId],
    'o:title' => $pageTitle,
    'o:slug' => $pageSlug,
    'o:is_public' => true,
    'o:block' => [
        [
            'o:layout' => 'html',
            'o:data' => [
                'html' => '<p>Items ingested in the latest preservation batch (DIP packages with browseable file trees).</p>',
            ],
            'o:position' => 1,
        ],
        [
            'o:layout' => 'browsePreview',
            'o:data' => [
                'resource_type' => 'items',
                'query' => $queryString,
                'heading' => '',
                'limit' => $previewLimit,
                'components' => ['resource-heading', 'resource-body', 'thumbnail'],
                'link-text' => '',
            ],
            'o:position' => 2,
        ],
    ],
];

if ($existingPageId) {
    try {
        $api->delete('site_pages', $existingPageId);
    } catch (Throwable $e) {
        fwrite(STDERR, "Could not replace existing page {$existingPageId}: {$e->getMessage()}\n");
    }
}
$page = $api->create('site_pages', $pagePayload)->getContent();
$pageId = $page->id();
$createdPage = true;

$navigation = $site->navigation();
if (!is_array($navigation)) {
    $navigation = [];
}

$hasNav = false;
foreach ($navigation as $entry) {
    if (($entry['type'] ?? '') === 'page' && (int) ($entry['data']['id'] ?? 0) === (int) $pageId) {
        $hasNav = true;
        break;
    }
}
if (!$hasNav) {
    $navigation[] = [
        'type' => 'page',
        'data' => [
            'label' => $navLabel,
            'id' => $pageId,
        ],
        'links' => [],
    ];
    $api->update('sites', $siteId, [
        'o:navigation' => $navigation,
    ], [], ['isPartial' => true]);
}

foreach ($items as $item) {
    $hasSet = false;
    foreach ($item->itemSets() as $set) {
        if ((int) $set->id() === (int) $itemSetId) {
            $hasSet = true;
            break;
        }
    }
    if ($hasSet) {
        continue;
    }
    try {
        $api->update(
            'items',
            $item->id(),
            ['o:item_set' => [['o:id' => $itemSetId]]],
            [],
            ['isPartial' => true, 'collectionAction' => 'append']
        );
    } catch (Throwable $e) {
        fwrite(STDERR, "Item {$item->id()} item_set append failed: {$e->getMessage()}\n");
    }
}

preg_match('/^\s*public_url:\s*(\S+)/m', file_get_contents($settingsPath), $urlMatch);
$publicBase = isset($urlMatch[1]) ? rtrim($urlMatch[1], '/') : '';

echo json_encode([
    'site_slug' => $siteSlug,
    'page_id' => $pageId,
    'page_slug' => $pageSlug,
    'page_created' => $createdPage,
    'item_set_id' => $itemSetId,
    'items_matched' => count($items),
    'public_page_url' => $publicBase
        ? "{$publicBase}/s/{$siteSlug}/page/{$pageSlug}"
        : "/s/{$siteSlug}/page/{$pageSlug}",
], JSON_PRETTY_PRINT) . "\n";
