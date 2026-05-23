<?php
if (session_status() == PHP_SESSION_NONE) {
    session_start();
}

function load_server_database_config() {
    $defaults = [
        'host' => '127.0.0.1',
        'port' => 3306,
        'name' => 'wot_emulator',
        'user' => 'root',
        'password' => '',
    ];
    $paths = [];
    $envPath = getenv('WOT_SERVER_CONFIG');
    if ($envPath) {
        $paths[] = $envPath;
    }
    $paths[] = __DIR__ . DIRECTORY_SEPARATOR . 'server.json';
    $paths[] = 'C:\\Users\\qwerty\\Documents\\GitHub\\worldoftanks-emulator-0.6.5\\config\\server.json';
    foreach ($paths as $path) {
        if (!is_string($path) || $path === '' || !is_file($path)) {
            continue;
        }
        $data = json_decode(file_get_contents($path), true);
        if (!is_array($data) || !isset($data['database']) || !is_array($data['database'])) {
            continue;
        }
        return array_merge($defaults, $data['database']);
    }
    return $defaults;
}

function db_column_exists($pdo, $table, $column) {
    $stmt = $pdo->prepare(
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND COLUMN_NAME = ?"
    );
    $stmt->execute([$table, $column]);
    return intval($stmt->fetchColumn()) > 0;
}

function ensure_site_schema($pdo) {
    static $ready = false;
    if ($ready) {
        return;
    }
    $pdo->exec("CREATE TABLE IF NOT EXISTS disabled_vehicles (vehicle_name VARCHAR(128) NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (vehicle_name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");
    $pdo->exec("CREATE TABLE IF NOT EXISTS account_vehicle_overrides (account_id BIGINT UNSIGNED NOT NULL, vehicle_name VARCHAR(128) NOT NULL, is_enabled TINYINT NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (account_id, vehicle_name), KEY idx_account_vehicle_overrides_vehicle (vehicle_name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");
    $pdo->exec("CREATE TABLE IF NOT EXISTS vehicle_access_events (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, scope VARCHAR(16) NOT NULL, account_id BIGINT UNSIGNED NULL, vehicle_name VARCHAR(128) NOT NULL, is_enabled TINYINT NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), KEY idx_vehicle_access_events_account (account_id), KEY idx_vehicle_access_events_scope (scope)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");
    if (db_column_exists($pdo, 'accounts', 'id') && !db_column_exists($pdo, 'accounts', 'is_admin')) {
        $pdo->exec("ALTER TABLE accounts ADD COLUMN is_admin TINYINT NOT NULL DEFAULT 0 AFTER clan_db_id");
    }
    if (db_column_exists($pdo, 'accounts', 'id') && !db_column_exists($pdo, 'accounts', 'email')) {
        $pdo->exec("ALTER TABLE accounts ADD COLUMN email VARCHAR(255) NULL DEFAULT NULL AFTER username");
        $pdo->exec("ALTER TABLE accounts ADD UNIQUE KEY uniq_email (email)");
    }
    if (!db_column_exists($pdo, 'disabled_vehicles', 'updated_at')) {
        $pdo->exec("ALTER TABLE disabled_vehicles ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP");
    }
    $ready = true;
}

$db_config = load_server_database_config();
$db_host = $db_config['host'] ?? '127.0.0.1';
$db_port = intval($db_config['port'] ?? 3306);
$db_name = $db_config['name'] ?? 'wot_emulator';
$db_user = $db_config['user'] ?? 'root';
$db_pass = $db_config['password'] ?? '';

try {
    $pdo = new PDO("mysql:host=$db_host;port=$db_port;dbname=$db_name;charset=utf8mb4", $db_user, $db_pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    ensure_site_schema($pdo);
} catch (PDOException $e) {
    die("Connection failed: " . $e->getMessage());
}
?>
