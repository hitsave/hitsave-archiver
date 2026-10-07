<?php
/**
 * Apply prod-like Ark settings on the test stack (NAAN 78322, internal ids).
 * Install/activate Common + Ark via omeka-s-cli before running this script.
 *
 * Usage: php scripts/ensure-omeka-ark-module.php
 */
declare(strict_types=1);

use Omeka\Entity\User;

require '/var/www/html/bootstrap.php';

$settingsPath = '/config/settings.yaml';
$arkCfg = readArkConfig($settingsPath);

$naan = (string) ($arkCfg['naan'] ?? '78322');
$nameProcessor = (string) ($arkCfg['name'] ?? 'internal');
$qualifier = (string) ($arkCfg['qualifier'] ?? 'internal');
$qualifierStatic = filter_var($arkCfg['qualifier_static'] ?? false, FILTER_VALIDATE_BOOLEAN);
$property = (string) ($arkCfg['property'] ?? 'dcterms:identifier');

$application = Omeka\Mvc\Application::init(
    require OMEKA_PATH . '/application/config/application.config.php'
);
$services = $application->getServiceManager();
$moduleManager = $services->get('Omeka\ModuleManager');
$settings = $services->get('Omeka\Settings');

$admin = $services->get('Omeka\EntityManager')->getRepository(User::class)
    ->findOneBy(['email' => 'admin@example.com']);
if (!$admin) {
    fwrite(STDERR, "Admin user admin@example.com not found.\n");
    exit(1);
}
$services->get('Omeka\AuthenticationService')->getStorage()->write($admin);

$result = [
    'common' => moduleStatus($moduleManager, 'Common'),
    'ark' => moduleStatus($moduleManager, 'Ark'),
    'settings' => [],
];

if (!$result['common']['present'] || !$result['ark']['present']) {
    fwrite(STDERR, "Common and/or Ark module code missing under volume/modules.\n");
    exit(1);
}

if ($result['common']['state'] !== 'active' || $result['ark']['state'] !== 'active') {
    fwrite(STDERR, "Common and Ark must be active. Run: omeka-s-cli module:install Common && omeka-s-cli module:activate Common && omeka-s-cli module:install Ark && omeka-s-cli module:activate Ark\n");
    exit(1);
}

applyArkSettings($settings, $naan, $nameProcessor, $qualifier, $qualifierStatic, $property, $result);

if (function_exists('opcache_reset')) {
    opcache_reset();
}

echo json_encode($result, JSON_PRETTY_PRINT) . "\n";

/**
 * @return array{present: bool, state: string}
 */
function moduleStatus($moduleManager, string $moduleId): array
{
    if (!moduleCodePresent($moduleId)) {
        return ['present' => false, 'state' => 'missing'];
    }
    $module = $moduleManager->getModule($moduleId);
    return ['present' => true, 'state' => $module->getState()];
}

function applyArkSettings(
    $settings,
    string $naan,
    string $nameProcessor,
    string $qualifier,
    bool $qualifierStatic,
    string $property,
    array &$result
): void {
    $pairs = [
        'ark_naan' => $naan,
        'ark_name' => $nameProcessor,
        'ark_qualifier' => $qualifier,
        'ark_qualifier_static' => $qualifierStatic,
        'ark_property' => $property,
    ];

    foreach ($pairs as $key => $value) {
        $current = $settings->get($key);
        if ($current === $value || ($current === null && $value === '')) {
            $result['settings'][$key] = ['changed' => false, 'value' => $current ?? $value];
            continue;
        }
        if (
            $key === 'ark_naan'
            && $current !== null
            && (string) $current !== ''
            && (string) $current !== $naan
            && (string) $current !== '99999'
        ) {
            $result['settings'][$key] = [
                'changed' => false,
                'skipped' => 'already_set',
                'value' => $current,
            ];
            continue;
        }
        $settings->set($key, $value);
        $result['settings'][$key] = ['changed' => true, 'value' => $value];
    }
}

/**
 * @return array<string, mixed>
 */
function readArkConfig(string $settingsPath): array
{
    if (!is_readable($settingsPath)) {
        return [];
    }
    $yaml = file_get_contents($settingsPath);
    if (!preg_match('/^ark:\s*\n((?:[ \t].*\n?)*)/m', $yaml, $block)) {
        return [];
    }
    $section = $block[1];
    $out = [];
    if (preg_match('/^\s*naan:\s*["\']?([^"\']+)["\']?\s*$/m', $section, $m)) {
        $out['naan'] = trim($m[1]);
    }
    if (preg_match('/^\s*name:\s*(\S+)\s*$/m', $section, $m)) {
        $out['name'] = $m[1];
    }
    if (preg_match('/^\s*qualifier:\s*(\S+)\s*$/m', $section, $m)) {
        $out['qualifier'] = $m[1];
    }
    if (preg_match('/^\s*qualifier_static:\s*(\S+)\s*$/m', $section, $m)) {
        $out['qualifier_static'] = $m[1];
    }
    if (preg_match('/^\s*property:\s*(\S+)\s*$/m', $section, $m)) {
        $out['property'] = $m[1];
    }
    return $out;
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
