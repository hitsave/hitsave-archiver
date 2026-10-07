<?php
/**
 * Replace the stored original .tar for a DIP media row (same filename; re-index on next view).
 *
 * Usage: php scripts/replace-dip-media-original.php <media_id> <path/to/package.tar>
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$mediaId = (int) ($argv[1] ?? 0);
$tarPath = $argv[2] ?? '';
if ($mediaId < 1 || !is_readable($tarPath)) {
    fwrite(STDERR, "Usage: php scripts/replace-dip-media-original.php <media_id> </path/to/package.tar>\n");
    exit(1);
}

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
try {
    $media = $api->read('media', $mediaId)->getContent();
} catch (Throwable $e) {
    fwrite(STDERR, "Media {$mediaId} not found.\n");
    exit(1);
}

if (!\OmekaDipViewer\Media\DipPackageMedia::isDipPackage($media->renderer())) {
    fwrite(STDERR, "Media {$mediaId} is not a DIP package.\n");
    exit(1);
}

$store = $services->get('Omeka\File\Store');
$storagePath = sprintf('original/%s', $media->filename());
$dest = $store->getLocalPath($storagePath);
if (!$dest) {
    fwrite(STDERR, "Could not resolve storage path for media {$mediaId}.\n");
    exit(1);
}

if (!copy($tarPath, $dest)) {
    fwrite(STDERR, "Copy failed: {$tarPath} -> {$dest}\n");
    exit(1);
}

$data = $media->mediaData();
unset($data['dip_index']);
$data['original_filename'] = basename($tarPath);
$api->update('media', $mediaId, ['o:data' => $data], [], ['isPartial' => true]);

$cacheDir = $services->get('OmekaDipViewer\Service\DipConfig')->getIndexCacheDirectory();
$pattern = $cacheDir . '/media-' . $mediaId . '-*.json';
foreach (glob($pattern) ?: [] as $file) {
    @unlink($file);
}

/** @var \OmekaDipViewer\Service\DipIndexService $indexService */
$indexService = $services->get('OmekaDipViewer\Service\DipIndexService');
$index = $indexService->getIndex($media);
$fileCount = is_array($index) ? count($index['files'] ?? []) : 0;
$treeChildren = is_array($index) ? count($index['tree']['children'] ?? []) : 0;

echo json_encode([
    'media_id' => $mediaId,
    'item_id' => $media->item()->id(),
    'storage_path' => $storagePath,
    'files_indexed' => $fileCount,
    'tree_top_folders' => $treeChildren,
    'tar' => basename($tarPath),
], JSON_PRETTY_PRINT) . "\n";
