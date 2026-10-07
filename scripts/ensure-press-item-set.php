<?php
/**
 * Ensure the Press and Marketing Materials item set exists (runs inside Omeka container).
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$settingsPath = '/config/settings.yaml';
$yaml = is_readable($settingsPath) ? file_get_contents($settingsPath) : '';
preg_match('/^\s*item_set_title:\s*(.+)$/m', $yaml, $m);
$title = isset($m[1]) ? trim($m[1], " \t\"'") : 'Press and Marketing Materials';

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$admin = $services->get('Omeka\EntityManager')->getRepository(User::class)
    ->findOneBy(['email' => 'admin@example.com']);
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$api = $services->get('Omeka\ApiManager');
$titleProperty = $api->search('properties', ['term' => 'dcterms:title'])->getContent()[0];

$existing = $api->search('item_sets', ['title' => $title])->getContent();
if ($existing) {
    echo json_encode(['item_set_id' => $existing[0]->id(), 'title' => $title, 'created' => false], JSON_PRETTY_PRINT) . "\n";
    exit(0);
}

$itemSet = $api->create('item_sets', [
    'dcterms:title' => [[
        'property_id' => $titleProperty->id(),
        'type' => 'literal',
        '@value' => $title,
    ]],
    'o:is_public' => false,
])->getContent();

echo json_encode(['item_set_id' => $itemSet->id(), 'title' => $title, 'created' => true], JSON_PRETTY_PRINT) . "\n";
