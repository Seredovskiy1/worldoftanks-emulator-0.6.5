<?php
require_once 'db.php';

if (!isset($_SESSION['user_id'])) {
    header('Location: login.php');
    exit;
}

$user_id = $_SESSION['user_id'];
$error = '';
$success = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['change_password'])) {
    if (!verify_csrf($_POST['csrf_token'] ?? '')) {
        $error = 'Сессия устарела. Обновите страницу.';
    } else {
    $current_password = $_POST['current_password'] ?? '';
    $new_password = $_POST['new_password'] ?? '';
    $new_password_confirm = $_POST['new_password_confirm'] ?? '';

    if (empty($current_password) || empty($new_password)) {
        $error = 'Пожалуйста, заполните все поля.';
    } elseif (strlen($new_password) < 6) {
        $error = 'Новый пароль должен быть не менее 6 символов.';
    } elseif (strlen($new_password) > 128) {
        $error = 'Новый пароль слишком длинный (максимум 128 символов).';
    } elseif ($new_password !== $new_password_confirm) {
        $error = 'Новые пароли не совпадают.';

    } else {
        try {
            $stmt = $pdo->prepare("SELECT password_hash FROM accounts WHERE id = ?");
            $stmt->execute([$user_id]);
            $current_hash = $stmt->fetchColumn();

            if (hash('sha256', $current_password) === $current_hash) {
                $new_hash = hash('sha256', $new_password);
                $update_stmt = $pdo->prepare("UPDATE accounts SET password_hash = ? WHERE id = ?");
                $update_stmt->execute([$new_hash, $user_id]);
                $success = 'Пароль успешно изменен!';
            } else {
                $error = 'Неверный текущий пароль.';
            }
        } catch (Exception $e) {
            error_log("Profile password change error: " . $e->getMessage());
            $error = 'Произошла внутренняя ошибка. Попробуйте позже.';
        }
    }
    }
}

try {
    $stmt = $pdo->prepare("SELECT username, credits, gold, free_xp, slots, berths, is_admin FROM accounts WHERE id = ?");
    $stmt->execute([$user_id]);
    $account = $stmt->fetch();

    $stmt = $pdo->prepare("SELECT total_battles, wins, losses, draws, frags, damage_dealt, damage_received, shots, hits, max_xp, max_damage, max_frags, total_xp FROM dossier WHERE account_id = ?");
    $stmt->execute([$user_id]);
    $dossier = $stmt->fetch();
} catch (Exception $e) {
    error_log("Profile load error: " . $e->getMessage());
    $account = null;
    $dossier = null;
}

if (!$account) {
    session_destroy();
    header('Location: login.php');
    exit;
}

if (!$dossier) {
    $dossier = [
        'total_battles' => 0, 'wins' => 0, 'losses' => 0, 'draws' => 0,
        'frags' => 0, 'damage_dealt' => 0, 'damage_received' => 0,
        'shots' => 0, 'hits' => 0, 'max_xp' => 0, 'max_damage' => 0,
        'max_frags' => 0, 'total_xp' => 0
    ];
}

$total_battles = intval($dossier['total_battles']);
$wins = intval($dossier['wins']);
$losses = intval($dossier['losses']);
$draws = intval($dossier['draws']);

$win_rate = $total_battles > 0 ? round(($wins / $total_battles) * 100, 2) : 0;
$loss_rate = $total_battles > 0 ? round(($losses / $total_battles) * 100, 2) : 0;
$draw_rate = $total_battles > 0 ? round(($draws / $total_battles) * 100, 2) : 0;

$shots = intval($dossier['shots']);
$hits = intval($dossier['hits']);
$hit_ratio = $shots > 0 ? round(($hits / $shots) * 100, 2) : 0;

$avg_dmg = $total_battles > 0 ? round(intval($dossier['damage_dealt']) / $total_battles) : 0;
$avg_xp = $total_battles > 0 ? round(intval($dossier['total_xp']) / $total_battles) : 0;
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Личный кабинет</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config={important:true,theme:{extend:{colors:{wot:{gold:'#e5a93b',dark:'#1a1a1c',panel:'#101011'}}}}}</script>
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content flex-col md:flex-row md:justify-between text-center md:text-left gap-1 md:gap-0">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Скачать</a>
        </div>
        <div class="top-bar-auth">
            <span>Здравствуйте, <a href="profile.php"><?php echo htmlspecialchars($account['username']); ?></a></span>
            <?php if (intval($account['is_admin']) === 1): ?>
                | <a href="admin.php">Админ-панель</a>
            <?php endif; ?>
            | <a href="logout.php" class="logout">Выйти</a>
        </div>
    </div>
</div>

<div class="header-banner h-[100px] md:h-[180px]">
    <div class="logo-container gap-2 md:gap-[18px]">
        <img src="images/logo.png" alt="Logo" class="logo-icon w-10 h-10 md:w-[72px] md:h-[72px]">
        <div class="logo-text-wrapper">
            <div class="logo-text text-xl md:text-4xl">World of Tanks</div>
            <div class="logo-subtext text-[9px] md:text-sm">Project Orion 0.6.5</div>
        </div>
    </div>
</div>

<div class="nav-container">
    <button class="nav-hamburger" onclick="document.getElementById('navMenu').classList.toggle('open')" aria-label="Меню">&#9776;</button>
    <ul class="nav-menu" id="navMenu">
        <li class="nav-item"><a href="index.php" class="nav-link">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link">Играть</a></li>
        <li class="nav-item"><a href="register.php" class="nav-link">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link active">Личный кабинет</a></li>
        <li class="nav-item"><a href="bugs.php" class="nav-link">Баг-репорты</a></li>
        <?php if (intval($account['is_admin']) === 1): ?>
            <li class="nav-item"><a href="admin.php" class="nav-link">Управление танками</a></li>
        <?php endif; ?>
    </ul>
</div>

<div class="main-layout flex-col md:flex-row">
    <div class="content-area w-full md:w-[70%]">
        <div class="card">
            <div class="card-header">
                <div class="profile-username text-lg md:text-2xl"><?php echo htmlspecialchars($account['username']); ?></div>
                <?php if (intval($account['is_admin']) === 1): ?>
                    <span class="profile-role">Администратор</span>
                <?php else: ?>
                    <span class="profile-role" style="background: #27ae60;">Игрок</span>
                <?php endif; ?>
            </div>
            <div class="card-body">
                <div class="profile-resources grid-cols-1 md:grid-cols-3">
                    <div class="resource-card">
                        <div class="resource-val resource-credits"><?php echo number_format($account['credits']); ?></div>
                        <div class="resource-label">Кредиты</div>
                    </div>
                    <div class="resource-card">
                        <div class="resource-val resource-gold"><?php echo number_format($account['gold']); ?></div>
                        <div class="resource-label">Золото</div>
                    </div>
                    <div class="resource-card">
                        <div class="resource-val resource-xp"><?php echo number_format($account['free_xp']); ?></div>
                        <div class="resource-label">Свободный опыт</div>
                    </div>
                </div>

                <div class="section-title">Статистика боев</div>

                <div class="stat-grid grid-cols-1 md:grid-cols-2">
                    <div>
                        <div class="stat-row">
                            <span class="stat-label">Сыграно боев:</span>
                            <span class="stat-value"><?php echo $total_battles; ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Победы (Win Rate):</span>
                            <span class="stat-value" style="color: #2ecc71;"><?php echo $wins; ?> (<?php echo $win_rate; ?>%)</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Поражения:</span>
                            <span class="stat-value" style="color: #e74c3c;"><?php echo $losses; ?> (<?php echo $loss_rate; ?>%)</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Ничьи:</span>
                            <span class="stat-value"><?php echo $draws; ?> (<?php echo $draw_rate; ?>%)</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Уничтожено врагов (Frags):</span>
                            <span class="stat-value"><?php echo number_format($dossier['frags']); ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Максимум уничтожено за бой:</span>
                            <span class="stat-value"><?php echo $dossier['max_frags']; ?></span>
                        </div>
                    </div>
                    <div>
                        <div class="stat-row">
                            <span class="stat-label">Нанесено урона:</span>
                            <span class="stat-value"><?php echo number_format($dossier['damage_dealt']); ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Получено урона:</span>
                            <span class="stat-value"><?php echo number_format($dossier['damage_received']); ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Средний урон за бой:</span>
                            <span class="stat-value"><?php echo number_format($avg_dmg); ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Точность (Hits/Shots):</span>
                            <span class="stat-value"><?php echo $hit_ratio; ?>% (<?php echo $hits; ?>/<?php echo $shots; ?>)</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Средний опыт за бой:</span>
                            <span class="stat-value"><?php echo number_format($avg_xp); ?></span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Максимальный опыт за бой:</span>
                            <span class="stat-value"><?php echo number_format($dossier['max_xp']); ?></span>
                        </div>
                    </div>
                </div>

                <div class="profile-extras">
                    <div class="resource-card">
                        <span class="stat-label">Слоты в ангаре:</span> <span class="stat-value"><?php echo $account['slots']; ?></span>
                    </div>
                    <div class="resource-card">
                        <span class="stat-label">Места в казарме:</span> <span class="stat-value"><?php echo $account['berths']; ?></span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="sidebar-area w-full md:w-[30%]">
        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Смена пароля</div>
            </div>
            <div class="card-body">
                <?php if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['change_password'])): ?>
                    <?php if (!empty($error)): ?>
                        <div class="alert alert-danger" style="padding: 8px 10px; font-size: 12px; margin-bottom: 15px;"><?php echo htmlspecialchars($error); ?></div>
                    <?php endif; ?>
                    <?php if (!empty($success)): ?>
                        <div class="alert alert-success" style="padding: 8px 10px; font-size: 12px; margin-bottom: 15px;"><?php echo htmlspecialchars($success); ?></div>
                    <?php endif; ?>
                <?php endif; ?>

                <form action="profile.php" method="POST">
                    <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token'] ?? ''); ?>">
                    <input type="hidden" name="change_password" value="1">
                    <div class="form-group">
                        <label for="current_password">Текущий пароль</label>
                        <input type="password" name="current_password" id="current_password" class="form-control" required autocomplete="current-password">
                    </div>
                    <div class="form-group">
                        <label for="new_password">Новый пароль</label>
                        <input type="password" name="new_password" id="new_password" class="form-control" required minlength="6" maxlength="128" autocomplete="new-password">
                    </div>
                    <div class="form-group">
                        <label for="new_password_confirm">Подтвердите пароль</label>
                        <input type="password" name="new_password_confirm" id="new_password_confirm" class="form-control" required minlength="6" maxlength="128" autocomplete="new-password">
                    </div>
                    <button type="submit" name="change_password" class="btn btn-primary" style="width: 100%; margin-top: 10px;">Обновить пароль</button>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="footer text-[11px] md:text-xs px-3 md:px-0">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<script src="sparks.js"></script>
</body>
</html>
