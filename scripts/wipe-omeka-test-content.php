<?php
/**
 * Delete all Omeka items (test reset). Does not remove modules or site config.
 *
 * Usage: php scripts/wipe-omeka-test-content.php [--dry-run]
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
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$api = $services->get('Omeka\ApiManager');
$deleted = [];
$page = 1;
do {
    $items = $api->search('items', ['page' => $page, 'per_page' => 100, 'sort_by' => 'id'])->getContent();
    if (!$items) {
        break;
    }
    foreach ($items as $item) {
        $id = (int) $item->id();
        if ($dryRun) {
            $deleted[] = $id;
            continue;
        }
        try {
            $api->delete('items', $id);
            $deleted[] = $id;
        } catch (Throwable $e) {
            fwrite(STDERR, "Item {$id}: {$e->getMessage()}\n");
        }
    }
    $page++;
} while (count($items) === 100);

echo json_encode(['dry_run' => $dryRun, 'deleted_count' => count($deleted), 'deleted' => $deleted], JSON_PRETTY_PRINT) . "\n";
