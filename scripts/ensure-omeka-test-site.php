<?php
/**
 * Ensure the public test site exists (runs inside the Omeka container).
 * Reads slug/title from /config/settings.yaml.
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$settingsPath = '/config/settings.yaml';
if (!is_readable($settingsPath)) {
    fwrite(STDERR, "Missing {$settingsPath}\n");
    exit(1);
}

$yaml = file_get_contents($settingsPath);
preg_match('/^\s*site_slug:\s*(\S+)/m', $yaml, $slugMatch);
preg_match('/^\s*site_title:\s*(.+)$/m', $yaml, $titleMatch);
$slug = $slugMatch[1] ?? 'hitsave-test';
$title = isset($titleMatch[1]) ? trim($titleMatch[1], " \t\"'") : 'HitSave DIP Viewer Test';

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
$existing = $api->search('sites', ['slug' => $slug])->getContent();
if ($existing) {
    $site = $existing[0];
    echo json_encode(['site_id' => $site->id(), 'slug' => $slug, 'created' => false], JSON_PRETTY_PRINT) . "\n";
    exit(0);
}

try {
    $site = $api->create('sites', [
        'o:slug' => $slug,
        'o:title' => $title,
        'o:theme' => 'default',
        'o:is_public' => true,
        'o:navigation' => [],
        'o:item_pool' => [],
    ])->getContent();
} catch (Throwable $e) {
    fwrite(STDERR, 'Site create failed: ' . $e->getMessage() . "\n");
    exit(1);
}

echo json_encode(['site_id' => $site->id(), 'slug' => $slug, 'created' => true], JSON_PRETTY_PRINT) . "\n";
