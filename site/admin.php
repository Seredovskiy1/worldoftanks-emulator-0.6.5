<?php
require_once 'db.php';

if (!isset($_SESSION['user_id']) || !isset($_SESSION['is_admin']) || !$_SESSION['is_admin']) {
    header('Location: login.php?error=admin');
    exit;
}

if (empty($_SESSION['csrf_token'])) {
    $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
}

function h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function json_out($payload) {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

function require_csrf() {
    $token = $_POST['csrf_token'] ?? '';
    if (!hash_equals($_SESSION['csrf_token'] ?? '', $token)) {
        json_out(['success' => false, 'error' => 'Сессия устарела. Обнови страницу.']);
    }
}

function vehicle_catalog_path() {
    $paths = [
        __DIR__ . DIRECTORY_SEPARATOR . '_vehicles.json',
    ];
    foreach ($paths as $path) {
        if (is_file($path)) {
            return $path;
        }
    }
    return $paths[0];
}

function vehicle_level($vehicle) {
    if (isset($vehicle['level'])) {
        return max(1, min(10, intval($vehicle['level'])));
    }
    foreach (($vehicle['tags'] ?? []) as $tag) {
        if (preg_match('/^tier(\d+)$/i', (string)$tag, $matches)) {
            return max(1, min(10, intval($matches[1])));
        }
    }
    return 1;
}

function load_vehicle_catalog() {
    $path = vehicle_catalog_path();
    if (!is_file($path)) {
        return [];
    }
    $data = json_decode(file_get_contents($path), true);
    if (!is_array($data) || !isset($data['vehicles']) || !is_array($data['vehicles'])) {
        return [];
    }
    $vehicles = [];
    foreach ($data['vehicles'] as $index => $vehicle) {
        if (!is_array($vehicle) || empty($vehicle['name'])) {
            continue;
        }
        $vehicle['inv_id'] = $index + 1;
        $vehicle['level_calculated'] = vehicle_level($vehicle);
        $vehicles[] = $vehicle;
    }
    usort($vehicles, function($a, $b) {
        return [$a['nation'] ?? '', $a['level_calculated'] ?? 1, $a['name'] ?? ''] <=> [$b['nation'] ?? '', $b['level_calculated'] ?? 1, $b['name'] ?? ''];
    });
    return $vehicles;
}

function normalize_vehicle_names($vehicle_name_set) {
    $raw = $_POST['vehicle_names'] ?? [];
    if (!is_array($raw)) {
        $raw = [$raw];
    }
    $names = [];
    foreach ($raw as $name) {
        $name = trim((string)$name);
        if ($name !== '' && isset($vehicle_name_set[$name])) {
            $names[$name] = true;
        }
    }
    return array_keys($names);
}

function insert_access_event($pdo, $scope, $account_id, $vehicle_name, $is_enabled) {
    $stmt = $pdo->prepare("INSERT INTO vehicle_access_events (scope, account_id, vehicle_name, is_enabled, created_at) VALUES (?, ?, ?, ?, ?)");
    $stmt->execute([$scope, $account_id ?: null, $vehicle_name, $is_enabled ? 1 : 0, date('Y-m-d H:i:s')]);
}

function apply_global_vehicle($pdo, $tank_name, $status) {
    $now = date('Y-m-d H:i:s');
    if ($status) {
        $stmt = $pdo->prepare("DELETE FROM disabled_vehicles WHERE vehicle_name = ?");
        $stmt->execute([$tank_name]);
    } else {
        $stmt = $pdo->prepare("INSERT INTO disabled_vehicles (vehicle_name, updated_at) VALUES (?, ?) ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)");
        $stmt->execute([$tank_name, $now]);
    }
}

function apply_account_vehicle($pdo, $account_id, $tank_name, $mode) {
    $now = date('Y-m-d H:i:s');
    if ($mode === 'inherit') {
        $stmt = $pdo->prepare("DELETE FROM account_vehicle_overrides WHERE account_id = ? AND vehicle_name = ?");
        $stmt->execute([$account_id, $tank_name]);
        return null;
    }
    $enabled = $mode === 'enabled';
    $stmt = $pdo->prepare("INSERT INTO account_vehicle_overrides (account_id, vehicle_name, is_enabled, updated_at) VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE is_enabled = VALUES(is_enabled), updated_at = VALUES(updated_at)");
    $stmt->execute([$account_id, $tank_name, $enabled ? 1 : 0, $now]);
    return $enabled;
}

function global_vehicle_enabled($pdo, $vehicle_name) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM disabled_vehicles WHERE vehicle_name = ?");
    $stmt->execute([$vehicle_name]);
    return intval($stmt->fetchColumn()) === 0;
}

function account_exists($pdo, $account_id) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM accounts WHERE id = ?");
    $stmt->execute([$account_id]);
    return intval($stmt->fetchColumn()) > 0;
}

function nation_label($nation) {
    $labels = [
        'ussr' => 'СССР',
        'germany' => 'Германия',
        'usa' => 'США',
        'china' => 'Китай',
        'france' => 'Франция',
        'uk' => 'Британия',
        'japan' => 'Япония',
        'czech' => 'Чехия',
        'sweden' => 'Швеция',
        'poland' => 'Польша',
        'italy' => 'Италия',
    ];
    return $labels[$nation] ?? $nation;
}

function class_label($class) {
    $labels = [
        'lightTank' => 'Легкий',
        'mediumTank' => 'Средний',
        'heavyTank' => 'Тяжелый',
        'AT-SPG' => 'ПТ-САУ',
        'SPG' => 'САУ',
    ];
    return $labels[$class] ?? $class;
}

$vehicles = load_vehicle_catalog();
$vehicle_name_set = [];
$nations = [];
$classes = [];
foreach ($vehicles as $vehicle) {
    $vehicle_name_set[$vehicle['name']] = true;
    if (!empty($vehicle['nation'])) {
        $nations[$vehicle['nation']] = true;
    }
    if (!empty($vehicle['vehicleClass'])) {
        $classes[$vehicle['vehicleClass']] = true;
    }
}
ksort($nations);
ksort($classes);

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_GET['ajax'])) {
    require_csrf();
    $action = $_POST['action'] ?? '';
    try {
        if ($action === 'set_global_vehicle') {
            $tank_name = trim($_POST['tank_name'] ?? '');
            $status = intval($_POST['status'] ?? 0) === 1;
            if ($tank_name === '' || !isset($vehicle_name_set[$tank_name])) {
                json_out(['success' => false, 'error' => 'Неизвестный танк.']);
            }
            apply_global_vehicle($pdo, $tank_name, $status);
            insert_access_event($pdo, 'global', null, $tank_name, $status);
            json_out(['success' => true, 'global_enabled' => $status]);
        }

        if ($action === 'bulk_global_vehicles') {
            $status = intval($_POST['status'] ?? 0) === 1;
            $names = normalize_vehicle_names($vehicle_name_set);
            if (empty($names)) {
                json_out(['success' => false, 'error' => 'Нет танков для этого действия.']);
            }
            $pdo->beginTransaction();
            foreach ($names as $name) {
                apply_global_vehicle($pdo, $name, $status);
            }
            insert_access_event($pdo, 'global', null, '*', $status);
            $pdo->commit();
            json_out(['success' => true, 'count' => count($names), 'global_enabled' => $status]);
        }

        if ($action === 'set_account_vehicle') {
            $account_id = intval($_POST['account_id'] ?? 0);
            $tank_name = trim($_POST['tank_name'] ?? '');
            $mode = $_POST['mode'] ?? 'inherit';
            if ($account_id <= 0 || !account_exists($pdo, $account_id) || $tank_name === '' || !isset($vehicle_name_set[$tank_name])) {
                json_out(['success' => false, 'error' => 'Неверные параметры.']);
            }
            if (!in_array($mode, ['inherit', 'enabled', 'disabled'], true)) {
                json_out(['success' => false, 'error' => 'Неизвестный режим.']);
            }
            $override = apply_account_vehicle($pdo, $account_id, $tank_name, $mode);
            $effective = $override === null ? global_vehicle_enabled($pdo, $tank_name) : $override;
            insert_access_event($pdo, 'account', $account_id, $tank_name, $effective);
            json_out(['success' => true, 'mode' => $mode, 'effective_enabled' => $effective]);
        }

        if ($action === 'bulk_account_vehicles') {
            $account_id = intval($_POST['account_id'] ?? 0);
            $mode = $_POST['mode'] ?? 'inherit';
            $names = normalize_vehicle_names($vehicle_name_set);
            if ($account_id <= 0 || !account_exists($pdo, $account_id)) {
                json_out(['success' => false, 'error' => 'Сначала выбери игрока.']);
            }
            if (!in_array($mode, ['inherit', 'enabled', 'disabled'], true)) {
                json_out(['success' => false, 'error' => 'Неизвестный режим.']);
            }
            if (empty($names)) {
                json_out(['success' => false, 'error' => 'Нет танков для этого действия.']);
            }
            $pdo->beginTransaction();
            foreach ($names as $name) {
                apply_account_vehicle($pdo, $account_id, $name, $mode);
            }
            insert_access_event($pdo, 'account', $account_id, '*', $mode !== 'disabled');
            $pdo->commit();
            json_out(['success' => true, 'count' => count($names), 'mode' => $mode]);
        }

        if ($action === 'reset_account_overrides') {
            $account_id = intval($_POST['account_id'] ?? 0);
            if ($account_id <= 0 || !account_exists($pdo, $account_id)) {
                json_out(['success' => false, 'error' => 'Игрок не выбран.']);
            }
            $stmt = $pdo->prepare("DELETE FROM account_vehicle_overrides WHERE account_id = ?");
            $stmt->execute([$account_id]);
            insert_access_event($pdo, 'account', $account_id, '*', 1);
            json_out(['success' => true]);
        }

        if ($action === 'enable_all_global') {
            $pdo->exec("DELETE FROM disabled_vehicles");
            insert_access_event($pdo, 'global', null, '*', 1);
            json_out(['success' => true]);
        }

        if ($action === 'save_account') {
            $account_id = intval($_POST['account_id'] ?? 0);
            if ($account_id <= 0 || !account_exists($pdo, $account_id)) {
                json_out(['success' => false, 'error' => 'Игрок не выбран.']);
            }
            $credits = max(0, intval($_POST['credits'] ?? 0));
            $gold = max(0, intval($_POST['gold'] ?? 0));
            $free_xp = max(0, intval($_POST['free_xp'] ?? 0));
            $slots = max(1, intval($_POST['slots'] ?? 1));
            $berths = max(0, intval($_POST['berths'] ?? 0));
            $is_admin = isset($_POST['is_admin']) ? 1 : 0;
            if ($account_id === intval($_SESSION['user_id'])) {
                $is_admin = 1;
            }
            $stmt = $pdo->prepare("UPDATE accounts SET credits = ?, gold = ?, free_xp = ?, slots = ?, berths = ?, is_admin = ? WHERE id = ?");
            $stmt->execute([$credits, $gold, $free_xp, $slots, $berths, $is_admin, $account_id]);
            json_out(['success' => true]);
        }

        json_out(['success' => false, 'error' => 'Неизвестное действие.']);
    } catch (Exception $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        error_log("Admin AJAX error: " . $e->getMessage());
        json_out(['success' => false, 'error' => 'Произошла внутренняя ошибка.']);
    }
}

$disabled_tanks = [];
$account_overrides = [];
$accounts = [];
$selected_account = null;
$search = trim($_GET['search'] ?? '');
$account_search = trim($_GET['account_search'] ?? '');
$filter_nation = $_GET['nation'] ?? '';
$filter_class = $_GET['class'] ?? '';
$filter_tier = $_GET['tier'] ?? '';
$filter_status = $_GET['status'] ?? '';
$selected_account_id = intval($_GET['account_id'] ?? 0);
$tab = $_GET['tab'] ?? 'vehicles';
$user_search = trim($_GET['user_search'] ?? '');
$user_page = max(1, intval($_GET['user_page'] ?? 1));

try {
    $disabled_tanks = $pdo->query("SELECT vehicle_name FROM disabled_vehicles")->fetchAll(PDO::FETCH_COLUMN);
} catch (Exception $e) {
    error_log("Admin disabled_tanks query: " . $e->getMessage());
    $disabled_tanks = [];
}
$disabled_set = array_flip($disabled_tanks);

try {
    if ($account_search !== '') {
        $stmt = $pdo->prepare("SELECT id, username, credits, gold, free_xp, slots, berths, is_admin, last_login FROM accounts WHERE username LIKE ? OR normalized_name LIKE ? OR id = ? ORDER BY last_login DESC LIMIT 80");
        $like = '%' . $account_search . '%';
        $stmt->execute([$like, $like, intval($account_search)]);
    } else {
        $stmt = $pdo->query("SELECT id, username, credits, gold, free_xp, slots, berths, is_admin, last_login FROM accounts ORDER BY last_login DESC LIMIT 80");
    }
    $accounts = $stmt->fetchAll();
} catch (Exception $e) {
    error_log("Admin accounts query: " . $e->getMessage());
    $accounts = [];
}

if ($selected_account_id <= 0 && !empty($accounts)) {
    $selected_account_id = intval($accounts[0]['id']);
}

if ($selected_account_id > 0) {
    try {
        $stmt = $pdo->prepare("SELECT id, username, credits, gold, free_xp, slots, berths, is_admin, last_login FROM accounts WHERE id = ?");
        $stmt->execute([$selected_account_id]);
        $selected_account = $stmt->fetch() ?: null;
        if ($selected_account) {
            $found = false;
            foreach ($accounts as $account) {
                if (intval($account['id']) === $selected_account_id) {
                    $found = true;
                    break;
                }
            }
            if (!$found) {
                array_unshift($accounts, $selected_account);
            }
            $stmt = $pdo->prepare("SELECT vehicle_name, is_enabled FROM account_vehicle_overrides WHERE account_id = ?");
            $stmt->execute([$selected_account_id]);
            foreach ($stmt->fetchAll() as $row) {
                $account_overrides[$row['vehicle_name']] = intval($row['is_enabled']) === 1;
            }
        }
    } catch (Exception $e) {
        error_log("Admin selected_account query: " . $e->getMessage());
        $selected_account = null;
    }
}

$total_accounts = 0;
$override_count = 0;
$event_count = 0;
try {
    $total_accounts = intval($pdo->query("SELECT COUNT(*) FROM accounts")->fetchColumn());
    $override_count = intval($pdo->query("SELECT COUNT(*) FROM account_vehicle_overrides")->fetchColumn());
    $event_count = intval($pdo->query("SELECT COUNT(*) FROM vehicle_access_events")->fetchColumn());
} catch (Exception $e) {
    error_log("Admin stats query: " . $e->getMessage());
}

$filtered_vehicles = [];
foreach ($vehicles as $vehicle) {
    $name = $vehicle['name'] ?? '';
    $nation = $vehicle['nation'] ?? '';
    $class = $vehicle['vehicleClass'] ?? '';
    $level = intval($vehicle['level_calculated'] ?? 1);
    $global_enabled = !isset($disabled_set[$name]);
    $has_override = array_key_exists($name, $account_overrides);
    $effective_enabled = $has_override ? $account_overrides[$name] : $global_enabled;

    if ($search !== '' && stripos($name, $search) === false) {
        continue;
    }
    if ($filter_nation !== '' && $nation !== $filter_nation) {
        continue;
    }
    if ($filter_class !== '' && $class !== $filter_class) {
        continue;
    }
    if ($filter_tier !== '' && $level !== intval($filter_tier)) {
        continue;
    }
    if ($filter_status === 'global_enabled' && !$global_enabled) {
        continue;
    }
    if ($filter_status === 'global_disabled' && $global_enabled) {
        continue;
    }
    if ($filter_status === 'effective_enabled' && !$effective_enabled) {
        continue;
    }
    if ($filter_status === 'effective_disabled' && $effective_enabled) {
        continue;
    }
    if ($filter_status === 'overridden' && !$has_override) {
        continue;
    }

    $vehicle['global_enabled'] = $global_enabled;
    $vehicle['has_override'] = $has_override;
    $vehicle['effective_enabled'] = $effective_enabled;
    $vehicle['override_mode'] = $has_override ? ($account_overrides[$name] ? 'enabled' : 'disabled') : 'inherit';
    $filtered_vehicles[] = $vehicle;
}

$filtered_vehicle_names = [];
foreach ($filtered_vehicles as $vehicle) {
    $filtered_vehicle_names[] = $vehicle['name'];
}

$page = max(1, intval($_GET['page'] ?? 1));
$limit = 30;
$total_items = count($filtered_vehicles);
$total_pages = max(1, intval(ceil($total_items / $limit)));
$page = min($page, $total_pages);
$offset = ($page - 1) * $limit;
$paginated_vehicles = array_slice($filtered_vehicles, $offset, $limit);
$csrf_token = $_SESSION['csrf_token'];
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Админ-панель</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config={important:true,theme:{extend:{colors:{wot:{gold:'#e5a93b',dark:'#1a1a1c',panel:'#101011'}}}}}</script>
    <style>
        body { overflow-x: hidden; }
        .top-bar, .header-banner, .nav-container, .main-layout, .footer { position: relative; z-index: 1; }
        .admin-shell { max-width: 1360px; width: 100%; }
        .admin-grid { display: grid; grid-template-columns: 292px 1fr; gap: 18px; width: 100%; }
        .admin-stack { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
        .admin-hero-strip { background: linear-gradient(90deg, rgba(209,39,17,0.18), rgba(229,169,59,0.12), rgba(15,15,16,0.96)); border: 1px solid #393026; border-radius: 4px; padding: 16px 18px; display: flex; align-items: center; justify-content: space-between; gap: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.38); }
        .admin-hero-title { color: #fff; font-size: 20px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; }
        .admin-hero-sub { color: #b7b0a7; font-size: 12px; margin-top: 2px; }
        .admin-live { display: flex; align-items: center; gap: 8px; color: #2ecc71; font-size: 12px; font-weight: 800; text-transform: uppercase; }
        .admin-live::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: #2ecc71; box-shadow: 0 0 14px rgba(46,204,113,0.95); animation: adminPulse 1.2s ease-in-out infinite; }
        .metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
        .metric { background: linear-gradient(180deg, rgba(24,24,26,0.96), rgba(13,13,14,0.96)); border: 1px solid #29292c; border-radius: 4px; padding: 14px; transition: transform 0.18s, border-color 0.18s, box-shadow 0.18s; }
        .metric:hover { transform: translateY(-2px); border-color: rgba(229,169,59,0.65); box-shadow: 0 12px 24px rgba(0,0,0,0.38), 0 0 18px rgba(229,169,59,0.12); }
        .metric-value { color: #ffffff; font-size: 24px; font-weight: 800; line-height: 1; text-shadow: 0 0 16px rgba(229,169,59,0.22); }
        .metric-label { color: #8c8c8c; font-size: 11px; text-transform: uppercase; margin-top: 6px; }
        .admin-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
        .admin-toolbar .form-control { width: auto; min-width: 148px; }
        .admin-toolbar .search-input { min-width: 240px; flex: 1; }
        .bulk-panel { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 10px; align-items: center; background: rgba(10,10,11,0.62); border: 1px solid #29292c; border-radius: 4px; padding: 12px; margin-bottom: 14px; }
        .bulk-title { color: #ffffff; font-weight: 800; text-transform: uppercase; font-size: 12px; }
        .bulk-sub { color: #8c8c8c; font-size: 12px; margin-top: 2px; }
        .bulk-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
        .account-list { display: flex; flex-direction: column; gap: 8px; max-height: 370px; overflow: auto; padding-right: 4px; }
        .account-link { display: block; padding: 10px 12px; background: #121214; border: 1px solid #29292c; border-radius: 4px; color: #d8d8d8; transition: transform 0.16s, border-color 0.16s, background 0.16s; }
        .account-link:hover { transform: translateX(2px); border-color: rgba(229,169,59,0.45); color: #ffffff; }
        .account-link.active { border-color: #e5a93b; color: #ffffff; background: #1d1910; box-shadow: inset 3px 0 0 #e5a93b; }
        .account-meta { display: block; color: #8c8c8c; font-size: 11px; margin-top: 2px; }
        .admin-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .admin-form-grid .form-group { margin-bottom: 0; }
        .admin-form-grid .full { grid-column: 1 / -1; }
        .muted { color: #8c8c8c; }
        .pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 800; text-transform: uppercase; white-space: nowrap; }
        .pill-on { background: rgba(46, 204, 113, 0.12); color: #2ecc71; border: 1px solid rgba(46, 204, 113, 0.4); }
        .pill-off { background: rgba(231, 76, 60, 0.12); color: #e74c3c; border: 1px solid rgba(231, 76, 60, 0.4); }
        .pill-neutral { background: rgba(149, 165, 166, 0.12); color: #bdc3c7; border: 1px solid rgba(149, 165, 166, 0.35); }
        .mini-select { min-width: 140px; }
        .table-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .notice-line { display: none; margin-bottom: 14px; }
        .notice-line.show { display: block; animation: noticeDrop 0.18s ease-out; }
        .danger-link { color: #e74c3c; background: none; border: 0; cursor: pointer; font: inherit; font-weight: 700; }
        .row-flash { animation: rowFlash 0.7s ease-out; }
        .tanks-table td { vertical-align: middle; }
        .tanks-table tbody tr { transition: background 0.16s, box-shadow 0.16s; }
        .tanks-table tbody tr:hover { box-shadow: inset 3px 0 0 rgba(229,169,59,0.8); }
        .tank-name { font-weight: 800; color: #ffffff; letter-spacing: 0.2px; }
        .btn-danger { background: linear-gradient(180deg, #5b1710 0%, #36100c 100%); color: #ffffff; border: 1px solid #8f2518; }
        .btn-danger:hover { background: linear-gradient(180deg, #7a2116 0%, #45130e 100%); color: #ffffff; }
        .btn-success { background: linear-gradient(180deg, #239a55 0%, #145f35 100%); color: #ffffff; border: 1px solid #2ecc71; }
        .btn-success:hover { background: linear-gradient(180deg, #2abf69 0%, #197242 100%); color: #ffffff; }
        .admin-empty { text-align: center; padding: 28px; color: #8c8c8c; }
        @media (max-width: 1080px) {
            .admin-grid { grid-template-columns: 1fr; }
            .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 760px) {
            .admin-hero-strip, .bulk-panel { grid-template-columns: 1fr; flex-direction: column; align-items: flex-start; }
            .admin-toolbar .form-control, .admin-toolbar .search-input { width: 100%; min-width: 0; }
            .admin-form-grid { grid-template-columns: 1fr; }
            .bulk-actions { justify-content: flex-start; }
            .metric-grid { grid-template-columns: 1fr; }
        }
    </style>
    <script>
        const csrfToken = <?php echo json_encode($csrf_token); ?>;
        const selectedAccountId = <?php echo intval($selected_account_id); ?>;
        const filteredVehicleNames = <?php echo json_encode($filtered_vehicle_names); ?>;

        function statusPill(enabled) {
            return enabled
                ? '<span class="pill pill-on">Включено</span>'
                : '<span class="pill pill-off">Выключено</span>';
        }

        function postAdmin(payload) {
            payload.append('csrf_token', csrfToken);
            return fetch('admin.php?ajax=1', { method: 'POST', body: payload })
                .then(response => response.json())
                .then(data => {
                    if (!data.success) {
                        throw new Error(data.error || 'Неизвестная ошибка');
                    }
                    return data;
                });
        }

        function flash(message, danger) {
            const box = document.getElementById('adminNotice');
            if (!box) {
                return;
            }
            box.className = 'notice-line show alert ' + (danger ? 'alert-danger' : 'alert-success');
            box.textContent = message;
            window.clearTimeout(window.adminNoticeTimer);
            window.adminNoticeTimer = window.setTimeout(() => box.classList.remove('show'), 2800);
        }

        function pulseRow(row) {
            if (!row) {
                return;
            }
            row.classList.remove('row-flash');
            void row.offsetWidth;
            row.classList.add('row-flash');
        }

        function updateEffective(row) {
            const override = row.dataset.overrideMode;
            const globalEnabled = row.dataset.globalEnabled === '1';
            const effective = override === 'inherit' ? globalEnabled : override === 'enabled';
            const effectiveCell = row.querySelector('.js-effective-status');
            if (effectiveCell) {
                effectiveCell.innerHTML = statusPill(effective);
            }
        }

        function toggleGlobal(input) {
            const row = input.closest('tr');
            const formData = new FormData();
            formData.append('action', 'set_global_vehicle');
            formData.append('tank_name', row.dataset.vehicleName);
            formData.append('status', input.checked ? '1' : '0');
            input.disabled = true;
            postAdmin(formData)
                .then(data => {
                    row.dataset.globalEnabled = data.global_enabled ? '1' : '0';
                    row.querySelector('.js-global-status').innerHTML = statusPill(data.global_enabled);
                    updateEffective(row);
                    pulseRow(row);
                    flash('Глобальный доступ обновлен.', false);
                })
                .catch(error => {
                    input.checked = !input.checked;
                    flash(error.message, true);
                })
                .finally(() => input.disabled = false);
        }

        function setPlayerMode(select) {
            const row = select.closest('tr');
            const oldValue = select.dataset.lastValue || 'inherit';
            const formData = new FormData();
            formData.append('action', 'set_account_vehicle');
            formData.append('account_id', selectedAccountId);
            formData.append('tank_name', row.dataset.vehicleName);
            formData.append('mode', select.value);
            select.disabled = true;
            postAdmin(formData)
                .then(data => {
                    row.dataset.overrideMode = data.mode;
                    select.dataset.lastValue = data.mode;
                    updateEffective(row);
                    pulseRow(row);
                    flash('Персональный доступ обновлен.', false);
                })
                .catch(error => {
                    select.value = oldValue;
                    flash(error.message, true);
                })
                .finally(() => select.disabled = false);
        }

        function appendVehicleNames(formData) {
            filteredVehicleNames.forEach(name => formData.append('vehicle_names[]', name));
            return filteredVehicleNames.length;
        }

        function bulkGlobal(status) {
            const formData = new FormData();
            const count = appendVehicleNames(formData);
            if (!count) {
                flash('Нет танков в текущем фильтре.', true);
                return;
            }
            const label = status ? 'включить' : 'выключить';
            if (!window.confirm('Глобально ' + label + ' ' + count + ' танков в текущем фильтре?')) {
                return;
            }
            formData.append('action', 'bulk_global_vehicles');
            formData.append('status', status ? '1' : '0');
            postAdmin(formData)
                .then(data => {
                    flash('Обновлено танков: ' + data.count + '.', false);
                    window.setTimeout(() => window.location.reload(), 450);
                })
                .catch(error => flash(error.message, true));
        }

        function bulkPlayer(mode) {
            if (!selectedAccountId) {
                flash('Сначала выбери игрока.', true);
                return;
            }
            const formData = new FormData();
            const count = appendVehicleNames(formData);
            if (!count) {
                flash('Нет танков в текущем фильтре.', true);
                return;
            }
            const labels = { inherit: 'вернуть наследование для', enabled: 'включить для игрока', disabled: 'выключить для игрока' };
            if (!window.confirm(labels[mode] + ' ' + count + ' танков в текущем фильтре?')) {
                return;
            }
            formData.append('action', 'bulk_account_vehicles');
            formData.append('account_id', selectedAccountId);
            formData.append('mode', mode);
            postAdmin(formData)
                .then(data => {
                    flash('Обновлено персональных правил: ' + data.count + '.', false);
                    window.setTimeout(() => window.location.reload(), 450);
                })
                .catch(error => flash(error.message, true));
        }

        function resetOverrides() {
            if (!selectedAccountId) {
                flash('Сначала выбери игрока.', true);
                return;
            }
            if (!window.confirm('Сбросить все персональные правила этого аккаунта?')) {
                return;
            }
            const formData = new FormData();
            formData.append('action', 'reset_account_overrides');
            formData.append('account_id', selectedAccountId);
            postAdmin(formData)
                .then(() => window.location.reload())
                .catch(error => flash(error.message, true));
        }

        function enableAllGlobal() {
            if (!window.confirm('Включить все танки глобально для сервера?')) {
                return;
            }
            const formData = new FormData();
            formData.append('action', 'enable_all_global');
            postAdmin(formData)
                .then(() => window.location.reload())
                .catch(error => flash(error.message, true));
        }

        function saveAccount(form) {
            const formData = new FormData(form);
            formData.append('action', 'save_account');
            postAdmin(formData)
                .then(() => flash('Аккаунт обновлен.', false))
                .catch(error => flash(error.message, true));
            return false;
        }

        function startAdminFx() {
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                return;
            }
            const canvas = document.getElementById('adminFx');
            if (!canvas) {
                return;
            }
            const ctx = canvas.getContext('2d');
            const particles = [];
            let width = 0;
            let height = 0;
            let frame = 0;
            function resize() {
                const ratio = Math.min(window.devicePixelRatio || 1, 2);
                width = window.innerWidth;
                height = window.innerHeight;
                canvas.width = Math.floor(width * ratio);
                canvas.height = Math.floor(height * ratio);
                canvas.style.width = width + 'px';
                canvas.style.height = height + 'px';
                ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            }
            function spawn() {
                particles.push({
                    x: Math.random() * width,
                    y: height + 24,
                    vx: -0.45 + Math.random() * 0.9,
                    vy: -0.8 - Math.random() * 1.8,
                    life: 60 + Math.random() * 90,
                    age: 0,
                    size: 1 + Math.random() * 2.6,
                    hue: Math.random() > 0.35 ? 34 : 12
                });
            }
            function draw() {
                frame++;
                ctx.clearRect(0, 0, width, height);
                ctx.globalCompositeOperation = 'lighter';
                if (frame % 2 === 0 && particles.length < 110) {
                    spawn();
                }
                for (let i = particles.length - 1; i >= 0; i--) {
                    const p = particles[i];
                    p.age++;
                    p.x += p.vx;
                    p.y += p.vy;
                    p.vy -= 0.004;
                    const alpha = Math.max(0, 1 - p.age / p.life);
                    ctx.strokeStyle = 'hsla(' + p.hue + ', 92%, 58%, ' + alpha * 0.62 + ')';
                    ctx.lineWidth = p.size;
                    ctx.beginPath();
                    ctx.moveTo(p.x, p.y);
                    ctx.lineTo(p.x - p.vx * 7, p.y - p.vy * 7);
                    ctx.stroke();
                    if (p.age >= p.life || p.y < -30) {
                        particles.splice(i, 1);
                    }
                }
                ctx.globalCompositeOperation = 'source-over';
                window.requestAnimationFrame(draw);
            }
            resize();
            window.addEventListener('resize', resize);
            draw();
        }

        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.js-player-mode').forEach(select => {
                select.dataset.lastValue = select.value;
            });
            startAdminFx();
        });
    </script>
</head>
<body>

<canvas class="admin-fx" id="adminFx"></canvas>

<div class="top-bar">
    <div class="top-bar-content flex-col md:flex-row md:justify-between text-center md:text-left gap-1 md:gap-0">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Загрузки</a>
            <a href="profile.php">Кабинет</a>
        </div>
        <div class="top-bar-auth">
            <span>Администратор: <a href="profile.php"><?php echo h($_SESSION['username'] ?? 'admin'); ?></a></span>
            | <a href="logout.php" class="logout">Выйти</a>
        </div>
    </div>
</div>

<div class="header-banner h-[100px] md:h-[180px]">
    <div class="logo-container gap-2 md:gap-[18px]">
        <img src="images/logo.png" alt="Logo" class="logo-icon w-10 h-10 md:w-[72px] md:h-[72px]">
        <div class="logo-text-wrapper">
            <div class="logo-text text-xl md:text-4xl">World of Tanks</div>
            <div class="logo-subtext text-[9px] md:text-sm">панель управления сервером</div>
        </div>
    </div>
</div>

<div class="nav-container">
    <button class="nav-hamburger" onclick="document.getElementById('navMenu').classList.toggle('open')" aria-label="Меню">&#9776;</button>
    <ul class="nav-menu" id="navMenu">
        <li class="nav-item"><a href="index.php" class="nav-link">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link">Играть</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Кабинет</a></li>
        <li class="nav-item"><a href="admin.php" class="nav-link active">Админ-панель</a></li>
    </ul>
</div>

<div class="main-layout admin-shell">
    <div class="admin-grid">
        <div class="admin-stack">
            <div class="card">
                <div class="card-header">
                    <div class="card-title text-sm md:text-lg">Игроки</div>
                </div>
                <div class="card-body">
                    <form action="admin.php" method="GET" class="search-box">
                        <input type="hidden" name="search" value="<?php echo h($search); ?>">
                        <input type="hidden" name="nation" value="<?php echo h($filter_nation); ?>">
                        <input type="hidden" name="class" value="<?php echo h($filter_class); ?>">
                        <input type="hidden" name="tier" value="<?php echo h($filter_tier); ?>">
                        <input type="hidden" name="status" value="<?php echo h($filter_status); ?>">
                        <input type="text" name="account_search" class="form-control search-input" placeholder="ID или ник" value="<?php echo h($account_search); ?>">
                        <button type="submit" class="btn btn-secondary">Найти</button>
                    </form>
                    <div class="account-list">
                        <?php foreach ($accounts as $account): ?>
                            <?php
                            $query = $_GET;
                            $query['account_id'] = intval($account['id']);
                            $query['page'] = 1;
                            $url = 'admin.php?' . http_build_query($query);
                            ?>
                            <a class="account-link <?php echo intval($account['id']) === $selected_account_id ? 'active' : ''; ?>" href="<?php echo h($url); ?>">
                                #<?php echo intval($account['id']); ?> <?php echo h($account['username']); ?>
                                <span class="account-meta"><?php echo intval($account['is_admin']) === 1 ? 'админ' : 'игрок'; ?> · <?php echo h($account['last_login'] ?? ''); ?></span>
                            </a>
                        <?php endforeach; ?>
                        <?php if (empty($accounts)): ?>
                            <span class="muted">Игроки не найдены.</span>
                        <?php endif; ?>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title text-sm md:text-lg">Аккаунт</div>
                </div>
                <div class="card-body">
                    <?php if ($selected_account): ?>
                        <form onsubmit="return saveAccount(this);" class="admin-form-grid">
                            <input type="hidden" name="account_id" value="<?php echo intval($selected_account['id']); ?>">
                            <div class="form-group">
                                <label>Кредиты</label>
                                <input type="number" name="credits" class="form-control" min="0" value="<?php echo intval($selected_account['credits']); ?>">
                            </div>
                            <div class="form-group">
                                <label>Золото</label>
                                <input type="number" name="gold" class="form-control" min="0" value="<?php echo intval($selected_account['gold']); ?>">
                            </div>
                            <div class="form-group">
                                <label>Свободный опыт</label>
                                <input type="number" name="free_xp" class="form-control" min="0" value="<?php echo intval($selected_account['free_xp']); ?>">
                            </div>
                            <div class="form-group">
                                <label>Слоты</label>
                                <input type="number" name="slots" class="form-control" min="1" value="<?php echo intval($selected_account['slots']); ?>">
                            </div>
                            <div class="form-group">
                                <label>Казарма</label>
                                <input type="number" name="berths" class="form-control" min="0" value="<?php echo intval($selected_account['berths']); ?>">
                            </div>
                            <div class="form-group">
                                <label>Права</label>
                                <label style="display:flex; align-items:center; gap:8px; color:#cccccc; text-transform:none; letter-spacing:0;">
                                    <input type="checkbox" name="is_admin" <?php echo intval($selected_account['is_admin']) === 1 ? 'checked' : ''; ?>>
                                    Администратор
                                </label>
                            </div>
                            <div class="full table-actions">
                                <button type="submit" class="btn btn-primary">Сохранить</button>
                                <button type="button" class="btn btn-secondary" onclick="resetOverrides()">Сбросить персональные правила</button>
                            </div>
                        </form>
                    <?php else: ?>
                        <span class="muted">Выбери игрока для персонального управления.</span>
                    <?php endif; ?>
                </div>
            </div>
        </div>

        <div class="admin-stack">
            <div class="admin-tabs" style="display:flex;gap:4px;margin-bottom:14px;">
                <a href="admin.php?tab=vehicles&amp;account_id=<?php echo intval($selected_account_id); ?>&amp;account_search=<?php echo h($account_search); ?>" class="btn <?php echo $tab === 'vehicles' ? 'btn-primary' : 'btn-secondary'; ?>" style="flex:1;text-align:center;">Контроль техники</a>
                <a href="admin.php?tab=users&amp;account_id=<?php echo intval($selected_account_id); ?>&amp;account_search=<?php echo h($account_search); ?>" class="btn <?php echo $tab === 'users' ? 'btn-primary' : 'btn-secondary'; ?>" style="flex:1;text-align:center;">Пользователи</a>
            </div>

            <?php if ($tab === 'vehicles'): ?>
            <div class="admin-hero-strip">
                <div>
                    <div class="admin-hero-title text-base md:text-xl">Контроль техники</div>
                    <div class="admin-hero-sub">Глобальное отключение работает для всех, а персональное правило может разрешить или заблокировать танк отдельному аккаунту.</div>
                </div>
                <div class="admin-live">live db</div>
            </div>

            <div class="metric-grid">
                <div class="metric">
                    <div class="metric-value"><?php echo $total_accounts; ?></div>
                    <div class="metric-label">аккаунтов</div>
                </div>
                <div class="metric">
                    <div class="metric-value"><?php echo count($vehicles); ?></div>
                    <div class="metric-label">танков в JSON</div>
                </div>
                <div class="metric">
                    <div class="metric-value"><?php echo count($disabled_tanks); ?></div>
                    <div class="metric-label">глобально выключено</div>
                </div>
                <div class="metric">
                    <div class="metric-value"><?php echo $override_count; ?></div>
                    <div class="metric-label">персональных правил</div>
                </div>
            </div>

            <div id="adminNotice" class="notice-line alert"></div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title text-sm md:text-lg">Доступ к технике</div>
                    <div class="muted">Событий: <?php echo $event_count; ?></div>
                </div>
                <div class="card-body">
                    <form action="admin.php" method="GET" class="admin-toolbar">
                        <input type="hidden" name="tab" value="vehicles">
                        <input type="hidden" name="account_id" value="<?php echo intval($selected_account_id); ?>">
                        <input type="hidden" name="account_search" value="<?php echo h($account_search); ?>">
                        <input type="text" name="search" class="form-control search-input" placeholder="Поиск танка" value="<?php echo h($search); ?>">
                        <select name="nation" class="form-control">
                            <option value="">Все нации</option>
                            <?php foreach (array_keys($nations) as $nation): ?>
                                <option value="<?php echo h($nation); ?>" <?php echo $filter_nation === $nation ? 'selected' : ''; ?>><?php echo h(nation_label($nation)); ?></option>
                            <?php endforeach; ?>
                        </select>
                        <select name="class" class="form-control">
                            <option value="">Все классы</option>
                            <?php foreach (array_keys($classes) as $class): ?>
                                <option value="<?php echo h($class); ?>" <?php echo $filter_class === $class ? 'selected' : ''; ?>><?php echo h(class_label($class)); ?></option>
                            <?php endforeach; ?>
                        </select>
                        <select name="tier" class="form-control">
                            <option value="">Все уровни</option>
                            <?php for ($tier = 1; $tier <= 10; $tier++): ?>
                                <option value="<?php echo $tier; ?>" <?php echo $filter_tier === (string)$tier ? 'selected' : ''; ?>><?php echo $tier; ?></option>
                            <?php endfor; ?>
                        </select>
                        <select name="status" class="form-control">
                            <option value="">Все статусы</option>
                            <option value="global_enabled" <?php echo $filter_status === 'global_enabled' ? 'selected' : ''; ?>>Глобально включены</option>
                            <option value="global_disabled" <?php echo $filter_status === 'global_disabled' ? 'selected' : ''; ?>>Глобально выключены</option>
                            <option value="effective_enabled" <?php echo $filter_status === 'effective_enabled' ? 'selected' : ''; ?>>Доступны игроку</option>
                            <option value="effective_disabled" <?php echo $filter_status === 'effective_disabled' ? 'selected' : ''; ?>>Закрыты игроку</option>
                            <option value="overridden" <?php echo $filter_status === 'overridden' ? 'selected' : ''; ?>>Есть персональное правило</option>
                        </select>
                        <button type="submit" class="btn btn-primary">Фильтр</button>
                        <a href="admin.php?tab=vehicles&amp;account_id=<?php echo intval($selected_account_id); ?>" class="btn btn-secondary">Сбросить</a>
                    </form>

                    <div class="bulk-panel">
                        <div>
                            <div class="bulk-title">Массовые действия по фильтру</div>
                            <div class="bulk-sub">Сейчас в фильтре: <?php echo $total_items; ?> танков. Действия применяются ко всем найденным, не только к этой странице.</div>
                        </div>
                        <div class="bulk-actions">
                            <button type="button" class="btn btn-success" onclick="bulkGlobal(true)">Глобально включить</button>
                            <button type="button" class="btn btn-danger" onclick="bulkGlobal(false)">Глобально выключить</button>
                        </div>
                        <div class="bulk-actions">
                            <button type="button" class="btn btn-secondary" onclick="bulkPlayer('inherit')">Как глобально</button>
                            <button type="button" class="btn btn-success" onclick="bulkPlayer('enabled')">Включить игроку</button>
                            <button type="button" class="btn btn-danger" onclick="bulkPlayer('disabled')">Выключить игроку</button>
                        </div>
                    </div>

                    <div class="bulk-panel">
                        <div>
                            <div class="bulk-title">Быстрое восстановление</div>
                            <div class="bulk-sub">Включает все танки глобально, но не удаляет персональные правила аккаунтов.</div>
                        </div>
                        <div class="bulk-actions">
                            <button type="button" class="btn btn-secondary" onclick="enableAllGlobal()">Включить все глобально</button>
                        </div>
                        <div></div>
                    </div>

                    <div class="tanks-table-container">
                        <table class="tanks-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Танк</th>
                                    <th>Нация</th>
                                    <th>Класс</th>
                                    <th>Уровень</th>
                                    <th>Для всех</th>
                                    <th>Для игрока</th>
                                    <th>Фактически</th>
                                </tr>
                            </thead>
                            <tbody>
                                <?php foreach ($paginated_vehicles as $vehicle): ?>
                                    <?php
                                    $name = $vehicle['name'] ?? '';
                                    $global_enabled = $vehicle['global_enabled'];
                                    $effective_enabled = $vehicle['effective_enabled'];
                                    $override_mode = $vehicle['override_mode'];
                                    ?>
                                    <tr data-vehicle-name="<?php echo h($name); ?>" data-global-enabled="<?php echo $global_enabled ? '1' : '0'; ?>" data-override-mode="<?php echo h($override_mode); ?>">
                                        <td><?php echo intval($vehicle['inv_id']); ?></td>
                                        <td class="tank-name"><?php echo h($name); ?></td>
                                        <td><span class="badge badge-<?php echo h($vehicle['nation'] ?? 'ussr'); ?>"><?php echo h(nation_label($vehicle['nation'] ?? '')); ?></span></td>
                                        <td><?php echo h(class_label($vehicle['vehicleClass'] ?? '')); ?></td>
                                        <td style="color:#e5a93b;font-weight:800;"><?php echo intval($vehicle['level_calculated']); ?></td>
                                        <td>
                                            <div class="table-actions">
                                                <span class="js-global-status"><?php echo $global_enabled ? '<span class="pill pill-on">Включено</span>' : '<span class="pill pill-off">Выключено</span>'; ?></span>
                                                <label class="switch">
                                                    <input type="checkbox" <?php echo $global_enabled ? 'checked' : ''; ?> onchange="toggleGlobal(this)">
                                                    <span class="slider"></span>
                                                </label>
                                            </div>
                                        </td>
                                        <td>
                                            <?php if ($selected_account): ?>
                                                <select class="form-control mini-select js-player-mode" onchange="setPlayerMode(this)">
                                                    <option value="inherit" <?php echo $override_mode === 'inherit' ? 'selected' : ''; ?>>Как глобально</option>
                                                    <option value="enabled" <?php echo $override_mode === 'enabled' ? 'selected' : ''; ?>>Включить</option>
                                                    <option value="disabled" <?php echo $override_mode === 'disabled' ? 'selected' : ''; ?>>Выключить</option>
                                                </select>
                                            <?php else: ?>
                                                <span class="pill pill-neutral">без игрока</span>
                                            <?php endif; ?>
                                        </td>
                                        <td class="js-effective-status"><?php echo $effective_enabled ? '<span class="pill pill-on">Включено</span>' : '<span class="pill pill-off">Выключено</span>'; ?></td>
                                    </tr>
                                <?php endforeach; ?>
                                <?php if (empty($paginated_vehicles)): ?>
                                    <tr>
                                        <td colspan="8" class="admin-empty">Танков по этим фильтрам нет.</td>
                                    </tr>
                                <?php endif; ?>
                            </tbody>
                        </table>
                    </div>

                    <?php if ($total_pages > 1): ?>
                        <div class="pagination">
                            <?php
                            $start = max(1, $page - 4);
                            $end = min($total_pages, $page + 4);
                            for ($i = $start; $i <= $end; $i++):
                                $query_params = $_GET;
                                $query_params['tab'] = 'vehicles';
                                $query_params['page'] = $i;
                                $link = 'admin.php?' . http_build_query($query_params);
                            ?>
                                <a href="<?php echo h($link); ?>" class="pagination-item <?php echo $page === $i ? 'active' : ''; ?>"><?php echo $i; ?></a>
                            <?php endfor; ?>
                        </div>
                    <?php endif; ?>
                </div>
            </div>
            <?php elseif ($tab === 'users'): ?>
            <?php
            $user_limit = 50;
            $user_where = '';
            $user_bind = [];
            if ($user_search !== '') {
                $user_where = "WHERE id = ? OR username LIKE ? OR email LIKE ? OR reg_ip LIKE ?";
                $like = '%' . $user_search . '%';
                $user_bind = [intval($user_search), $like, $like, $like];
            }
            try {
                $count_stmt = $pdo->prepare("SELECT COUNT(*) FROM accounts $user_where");
                $count_stmt->execute($user_bind);
                $user_total = intval($count_stmt->fetchColumn());
            } catch (Exception $e) {
                $user_total = 0;
            }
            $user_total_pages = max(1, intval(ceil($user_total / $user_limit)));
            $user_page = min($user_page, $user_total_pages);
            $user_offset = ($user_page - 1) * $user_limit;
            try {
                $stmt = $pdo->prepare("SELECT id, username, email, reg_ip, is_admin, created_at, last_login FROM accounts $user_where ORDER BY id ASC LIMIT $user_limit OFFSET $user_offset");
                $stmt->execute($user_bind);
                $all_users = $stmt->fetchAll();
            } catch (Exception $e) {
                $all_users = [];
            }
            ?>
            <div class="admin-hero-strip">
                <div>
                    <div class="admin-hero-title text-base md:text-xl">Пользователи</div>
                    <div class="admin-hero-sub">Страница <?php echo $user_page; ?> из <?php echo $user_total_pages; ?> · всего <?php echo $user_total; ?> аккаунтов</div>
                </div>
                <div class="admin-live"><?php echo $total_accounts; ?> аккаунтов</div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title text-sm md:text-lg">Все пользователи</div>
                </div>
                <div class="card-body">
                    <form action="admin.php" method="GET" class="admin-toolbar">
                        <input type="hidden" name="tab" value="users">
                        <input type="hidden" name="user_page" value="1">
                        <input type="text" name="user_search" class="form-control search-input" placeholder="ID, логин, email или IP" value="<?php echo h($user_search); ?>">
                        <button type="submit" class="btn btn-primary">Найти</button>
                        <a href="admin.php?tab=users" class="btn btn-secondary">Сбросить</a>
                    </form>

                    <div class="tanks-table-container">
                        <table class="tanks-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Логин</th>
                                    <th>Email</th>
                                    <th>IP</th>
                                    <th>Админ</th>
                                    <th>Регистрация</th>
                                    <th>Последний вход</th>
                                </tr>
                            </thead>
                            <tbody>
                                <?php foreach ($all_users as $u): ?>
                                    <tr>
                                        <td><?php echo intval($u['id']); ?></td>
                                        <td class="tank-name"><?php echo h($u['username']); ?></td>
                                        <td style="color:#b7b0a7;font-size:12px;"><?php echo h($u['email'] ?? '-'); ?></td>
                                        <td style="font-family:monospace;font-size:12px;color:#8c8c8c;"><?php echo h($u['reg_ip'] ?? '-'); ?></td>
                                        <td><?php echo intval($u['is_admin']) === 1 ? '<span class="pill pill-on">да</span>' : '<span class="pill pill-off">нет</span>'; ?></td>
                                        <td style="font-size:12px;color:#8c8c8c;"><?php echo h($u['created_at'] ?? '-'); ?></td>
                                        <td style="font-size:12px;color:#8c8c8c;"><?php echo h($u['last_login'] ?? '-'); ?></td>
                                    </tr>
                                <?php endforeach; ?>
                                <?php if (empty($all_users)): ?>
                                    <tr>
                                        <td colspan="7" class="admin-empty">Пользователи не найдены.</td>
                                    </tr>
                                <?php endif; ?>
                            </tbody>
                        </table>
                    </div>

                    <?php if ($user_total_pages > 1): ?>
                        <div class="pagination">
                            <?php
                            $u_start = max(1, $user_page - 4);
                            $u_end = min($user_total_pages, $user_page + 4);
                            for ($i = $u_start; $i <= $u_end; $i++):
                            ?>
                                <a href="admin.php?tab=users&amp;user_page=<?php echo $i; ?>&amp;user_search=<?php echo h($user_search); ?>" class="pagination-item <?php echo $user_page === $i ? 'active' : ''; ?>"><?php echo $i; ?></a>
                            <?php endfor; ?>
                        </div>
                    <?php endif; ?>
                </div>
            </div>
            <?php endif; ?>
        </div>
    </div>
</div>

<div class="footer text-[11px] md:text-xs px-3 md:px-0">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Админ-панель управляет той же базой, что и сервер.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

</body>
</html>
