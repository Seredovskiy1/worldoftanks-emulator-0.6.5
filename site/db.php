<?php
session_set_cookie_params([
    'lifetime' => 0,
    'path' => '/',
    'domain' => '',
    'secure' => false,
    'httponly' => true,
    'samesite' => 'Lax',
]);
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
    $paths[] = __DIR__ . DIRECTORY_SEPARATOR . 'server.json';
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
    if (db_column_exists($pdo, 'accounts', 'id') && !db_column_exists($pdo, 'accounts', 'reg_ip')) {
        $pdo->exec("ALTER TABLE accounts ADD COLUMN reg_ip VARCHAR(45) NULL DEFAULT NULL AFTER email");
    }
    if (db_column_exists($pdo, 'accounts', 'id') && !db_column_exists($pdo, 'accounts', 'is_banned_reports')) {
        $pdo->exec("ALTER TABLE accounts ADD COLUMN is_banned_reports TINYINT NOT NULL DEFAULT 0 AFTER is_admin");
    }
    
    // Bug reports tables
    $pdo->exec("CREATE TABLE IF NOT EXISTS bug_reports (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, account_id BIGINT UNSIGNED NOT NULL, title VARCHAR(255) NOT NULL, description TEXT NOT NULL, status ENUM('open', 'in_progress', 'resolved', 'closed') NOT NULL DEFAULT 'open', is_approved TINYINT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, PRIMARY KEY (id), KEY idx_bug_reports_account (account_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");
    $pdo->exec("CREATE TABLE IF NOT EXISTS bug_comments (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, bug_id BIGINT UNSIGNED NOT NULL, account_id BIGINT UNSIGNED NOT NULL, comment TEXT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), KEY idx_bug_comments_bug (bug_id), KEY idx_bug_comments_account (account_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");

    if (db_column_exists($pdo, 'bug_reports', 'id') && !db_column_exists($pdo, 'bug_reports', 'is_approved')) {
        $pdo->exec("ALTER TABLE bug_reports ADD COLUMN is_approved TINYINT NOT NULL DEFAULT 0 AFTER status");
    }

    $ready = true;
}

if (empty($_SESSION['csrf_token'])) {
    $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
}

function verify_csrf($token) {
    return !empty($_SESSION['csrf_token']) && !empty($token) && hash_equals($_SESSION['csrf_token'], $token);
}

function get_client_ip() {
    if (!empty($_SERVER['HTTP_CF_CONNECTING_IP'])) {
        return $_SERVER['HTTP_CF_CONNECTING_IP'];
    }
    if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) {
        $ips = explode(',', $_SERVER['HTTP_X_FORWARDED_FOR']);
        return trim($ips[0]);
    }
    return $_SERVER['REMOTE_ADDR'] ?? '';
}

function security_headers() {
    header('X-Frame-Options: DENY');
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: strict-origin-when-cross-origin');
    header("Content-Security-Policy: default-src 'self'; script-src 'self' https://cdn.tailwindcss.com https://www.google.com https://www.gstatic.com 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src https://www.google.com; img-src 'self' data:; connect-src 'self'; font-src 'self'");
}

security_headers();

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
    error_log("DB connection error: " . $e->getMessage());
    die("Connection failed. Please check server configuration.");
}
?>
