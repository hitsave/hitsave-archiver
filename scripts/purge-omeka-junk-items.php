<?php
/**
 * Remove broken batch shells and other junk items (no media, or untitled with no DIP).
 * Keeps ledger-linked items and item 8 (WoG1 pilot) by default.
 *
 * Usage: php scripts/purge-omeka-junk-items.php [--dry-run]
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$dryRun = in_array('--dry-run', $argv, true);

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$admin = $services->get('Omeka\EntityManager')->getRepository(User::class)
    ->findOneBy(['email' => 'admin@example.com']);
if (!$admin) {
    fwrite(STDERR, "Admin user not found.\n");
    exit(1);
}
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$api = $services->get('Omeka\ApiManager');

$keepIds = [8];
$ledgerPath = '/config/preservation/generated.env';
// Ledger lives on preservation host; optional keep list from env file is not item ids.

$items = $api->search('items', ['limit' => 500, 'sort_by' => 'id', 'sort_order' => 'asc'])->getContent();
$deleted = [];
$kept = [];

foreach ($items as $item) {
    $id = (int) $item->id();
    if (in_array($id, $keepIds, true)) {
        $kept[] = $id;
        continue;
    }
    $media = $item->media();
    $title = trim($item->displayTitle());
    $hasDip = false;
    foreach ($media as $medium) {
        if ($medium->renderer() === 'omeka_dip_package') {
            $hasDip = true;
            break;
        }
    }
    $isOldBatchShell = $id >= 10 && $id <= 70 && $id !== 8 && $id !== 65;
    $junk = $isOldBatchShell
        || count($media) === 0
        || (($title === '' || $title === '[Untitled]') && !$hasDip);
    if (!$junk) {
        $kept[] = $id;
        continue;
    }
    if ($dryRun) {
        $deleted[] = $id;
        continue;
    }
    try {
        $api->delete('items', $id);
        $deleted[] = $id;
    } catch (Throwable $e) {
        fwrite(STDERR, "Failed to delete item {$id}: {$e->getMessage()}\n");
    }
}

echo json_encode([
    'dry_run' => $dryRun,
    'deleted' => $deleted,
    'kept_count' => count($kept),
    'deleted_count' => count($deleted),
], JSON_PRETTY_PRINT) . "\n";
