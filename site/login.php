<?php
require_once 'db.php';
require_once 'recaptcha.php';

if (isset($_SESSION['user_id'])) {
    header('Location: profile.php');
    exit;
}

$error = '';
$max_attempts = 10;
$lockout_time = 900;
$attempts_key = 'login_attempts_' . get_client_ip();
$attempts = $_SESSION[$attempts_key] ?? ['count' => 0, 'time' => 0];
if ($attempts['count'] >= $max_attempts && time() - $attempts['time'] < $lockout_time) {
    $error = 'Слишком много попыток. Попробуйте через 15 минут.';
} elseif ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verify_csrf($_POST['csrf_token'] ?? '')) {
        $error = 'Сессия устарела. Обновите страницу.';
    } else {
    $username = trim($_POST['username'] ?? '');
    $password = $_POST['password'] ?? '';

    if (empty($username) || empty($password)) {
        $error = 'Пожалуйста, заполните все поля.';
    } elseif (!verify_recaptcha($_POST['g-recaptcha-response'] ?? '')) {
        $error = 'Пожалуйста, подтвердите, что вы не робот.';
    } else {
        try {
            $stmt = $pdo->prepare("SELECT id, username, password_hash, is_admin FROM accounts WHERE username = ? OR normalized_name = ? OR email = ?");
            $stmt->execute([$username, $username, $username]);
            $user = $stmt->fetch();

            if ($user && hash('sha256', $password) === $user['password_hash']) {
                session_regenerate_id(true);
                $_SESSION['user_id'] = $user['id'];
                $_SESSION['username'] = $user['username'];
                $_SESSION['is_admin'] = (intval($user['is_admin']) === 1);

                $now = date('Y-m-d H:i:s');
                $reg_ip = get_client_ip();
                $update_stmt = $pdo->prepare("UPDATE accounts SET last_login = ?, reg_ip = ? WHERE id = ?");
                $update_stmt->execute([$now, $reg_ip, $user['id']]);

                unset($_SESSION[$attempts_key]);
                header('Location: profile.php');
                exit;
            } else {
                $attempts['count']++;
                $attempts['time'] = time();
                $_SESSION[$attempts_key] = $attempts;
                $error = 'Неверное имя пользователя или пароль.';
            }
        } catch (Exception $e) {
            error_log("Login DB error: " . $e->getMessage());
            $error = 'Произошла внутренняя ошибка. Попробуйте позже.';
        }
    }
    }
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Вход</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>
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
            <a href="login.php">Войти</a> или <a href="register.php">Создать аккаунт</a>
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
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
        <li class="nav-item"><a href="bugs.php" class="nav-link">Баг-репорты</a></li>
    </ul>
</div>

<style>
    .auth-card .form-control { padding: 15px 20px; font-size: 16px; }
    .auth-card .btn { font-size: 16px; padding: 15px 25px; }
    .auth-card label { font-size: 14px; margin-bottom: 8px; }
    .auth-card .card-title { font-size: 24px; }
    .auth-card .g-recaptcha { transform: scale(1.1); transform-origin: left center; margin-top: 5px; }
    @media (max-width: 480px) { .auth-card .g-recaptcha { transform: scale(0.85); } }
</style>

<div class="main-layout flex-col items-center" style="justify-content: center;">
    <div class="content-area w-full auth-card" style="max-width: 800px;">
        <div class="card">
            <div class="card-header">
                <div class="card-title">Авторизация</div>
            </div>
            <div class="card-body" style="padding: 30px;">
                <?php if (!empty($error)): ?>
                    <div class="alert alert-danger"><?php echo htmlspecialchars($error); ?></div>
                <?php endif; ?>
                
                <form action="login.php" method="POST">
                    <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token'] ?? ''); ?>">
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="username">Логин или Email</label>
                        <input type="text" name="username" id="username" class="form-control" placeholder="Введите логин..." required autocomplete="username">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="password">Пароль</label>
                        <input type="password" name="password" id="password" class="form-control" placeholder="Введите пароль..." required autocomplete="current-password">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <div class="g-recaptcha" data-sitekey="<?php echo htmlspecialchars(RECAPTCHA_SITE_KEY, ENT_QUOTES, 'UTF-8'); ?>"></div>
                    </div>
                    <div class="form-actions" style="margin-top: 35px;">
                        <a href="register.php" style="font-size: 15px;">Нет аккаунта? Создать</a>
                        <button type="submit" class="btn btn-primary" style="width: 200px;">Войти</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="footer">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<script src="sparks.js"></script>
</body>
</html>