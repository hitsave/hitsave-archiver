<?php
/**
 * Sync OmekaDipViewer DB version with module.ini and deactivate modules whose code is gone.
 *
 * Usage: php scripts/ensure-omeka-dip-viewer-upgrade.php
 */
declare(strict_types=1);

use Omeka\Entity\Module as ModuleEntity;
use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$moduleManager = $services->get('Omeka\ModuleManager');
$entityManager = $services->get('Omeka\EntityManager');

$deactivated = deactivateMissingModules($entityManager, [
    'HitSaveDipViewer',
    'ArchivematicaConnector',
    'Exports',
]);

$module = $moduleManager->getModule('OmekaDipViewer');
if (!$module) {
    fwrite(STDERR, "OmekaDipViewer module not installed.\n");
    exit(1);
}

$iniVersion = (string) ($module->getIni('version') ?? '');
$dbEntity = $entityManager->find(ModuleEntity::class, 'OmekaDipViewer');
$dbVersion = $dbEntity ? (string) $dbEntity->getVersion() : '';

$state = $module->getState();
$needsVersionSync = $iniVersion !== '' && $iniVersion !== $dbVersion;
$shouldUpgrade = $state === 'needs_upgrade' || $needsVersionSync;

$result = [
    'module' => 'OmekaDipViewer',
    'state' => $state,
    'ini_version' => $iniVersion,
    'db_version' => $dbVersion,
    'deactivated_modules' => $deactivated,
    'upgraded' => false,
];

if (!$shouldUpgrade) {
    if (function_exists('opcache_reset')) {
        opcache_reset();
    }
    echo json_encode($result) . "\n";
    exit(0);
}

$admin = $entityManager->getRepository(User::class)->findOneBy(['email' => 'admin@example.com']);
if (!$admin) {
    fwrite(STDERR, "Admin user not found.\n");
    exit(1);
}
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$moduleManager->upgrade($module);
$result['state'] = $moduleManager->getModule('OmekaDipViewer')->getState();
$result['db_version'] = $entityManager->find(ModuleEntity::class, 'OmekaDipViewer')?->getVersion();
$result['upgraded'] = true;

if (function_exists('opcache_reset')) {
    opcache_reset();
}

echo json_encode($result) . "\n";

/**
 * @param list<string> $moduleIds
 * @return list<string>
 */
function deactivateMissingModules($entityManager, array $moduleIds): array
{
    $deactivated = [];
    foreach ($moduleIds as $moduleId) {
        if (moduleCodePresent($moduleId)) {
            continue;
        }
        $entity = $entityManager->find(ModuleEntity::class, $moduleId);
        if (!$entity || !$entity->isActive()) {
            continue;
        }
        $entity->setIsActive(false);
        $deactivated[] = $moduleId;
    }
    if ($deactivated) {
        $entityManager->flush();
    }
    return $deactivated;
}

function moduleCodePresent(string $moduleId): bool
{
    $candidates = [
        OMEKA_PATH . '/modules/' . $moduleId,
        OMEKA_PATH . '/volume/modules/' . $moduleId,
    ];
    foreach ($candidates as $path) {
        if (is_dir($path) && is_readable($path . '/Module.php')) {
            return true;
        }
    }
    return false;
}
