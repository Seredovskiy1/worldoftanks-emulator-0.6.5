<?php
require_once 'db.php';
require_once 'recaptcha.php';

if (isset($_SESSION['user_id'])) {
    header('Location: profile.php');
    exit;
}

$error = '';
$success = '';
$max_attempts = 5;
$lockout_time = 900;
$reg_attempts_key = 'reg_attempts_' . get_client_ip();
$reg_attempts = $_SESSION[$reg_attempts_key] ?? ['count' => 0, 'time' => 0];
if ($reg_attempts['count'] >= $max_attempts && time() - $reg_attempts['time'] < $lockout_time) {
    $error = 'Слишком много попыток регистрации. Попробуйте через 15 минут.';
}

if (empty($error) && !function_exists('normalize_login_name')) {
    function normalize_login_name($username) {
        $username = trim($username);
        if (strpos($username, '@') !== false) {
            $parts = explode('@', $username);
            $username = $parts[0];
        }
        $filtered = '';
        for ($i = 0; $i < strlen($username); $i++) {
            $ch = $username[$i];
            if (ctype_alnum($ch) || $ch === '_' || $ch === '-' || $ch === '.') {
                $filtered .= $ch;
            }
        }
        $filtered = substr($filtered, 0, 24);
        return empty($filtered) ? 'player' : $filtered;
    }
}

if ($error === '' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verify_csrf($_POST['csrf_token'] ?? '')) {
        $error = 'Сессия устарела. Обновите страницу.';
    } else {
    $username = trim($_POST['username'] ?? '');
    $email = trim($_POST['email'] ?? '');
    $password = $_POST['password'] ?? '';
    $password_confirm = $_POST['password_confirm'] ?? '';

    if (empty($username) || empty($email) || empty($password)) {
        $error = 'Пожалуйста, заполните все поля.';
    } elseif (strlen($username) < 3 || strlen($username) > 24) {
        $error = 'Никнейм должен быть от 3 до 24 символов.';
    } elseif (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        $error = 'Пожалуйста, введите корректный адрес электронной почты.';
    } elseif (strlen($password) < 6) {
        $error = 'Пароль должен быть не менее 6 символов.';
    } elseif (strlen($password) > 128) {
        $error = 'Пароль слишком длинный (максимум 128 символов).';
    } elseif ($password !== $password_confirm) {
        $error = 'Пароли не совпадают.';
    } elseif (!verify_recaptcha($_POST['g-recaptcha-response'] ?? '')) {
        $error = 'Пожалуйста, подтвердите, что вы не робот.';
    } else {
        $reg_ip = get_client_ip();
        $normalized = normalize_login_name($username);

        try {
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM accounts WHERE normalized_name = ? OR username = ? OR email = ?");
            $stmt->execute([$normalized, $username, $email]);
            if ($stmt->fetchColumn() > 0) {
                $error = 'Этот никнейм или email уже зарегистрирован.';
            } else {
                $stmt = $pdo->prepare("SELECT COUNT(*) FROM accounts WHERE reg_ip = ?");
                $stmt->execute([$reg_ip]);
                if ($stmt->fetchColumn() > 0) {
                    $error = 'С одного IP можно зарегистрировать только один аккаунт.';
                } else {
                $password_hash = hash('sha256', $password);
                $now = date('Y-m-d H:i:s');

                $pdo->beginTransaction();

                $stmt = $pdo->prepare("INSERT INTO accounts (username, email, normalized_name, password_hash, reg_ip, created_at, last_login) VALUES (?, ?, ?, ?, ?, ?, ?)");
                $stmt->execute([$username, $email, $normalized, $password_hash, $reg_ip, $now, $now]);
                
                $new_id = $pdo->lastInsertId();

                $stmt = $pdo->prepare("INSERT INTO dossier (account_id) VALUES (?)");
                $stmt->execute([$new_id]);

                $pdo->commit();

                unset($_SESSION[$reg_attempts_key]);
                $success = 'Регистрация успешна! Теперь вы можете войти.';
            }
            }
        } catch (Exception $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            $reg_attempts['count']++;
            $reg_attempts['time'] = time();
            $_SESSION[$reg_attempts_key] = $reg_attempts;
            error_log("Register DB error: " . $e->getMessage());
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
    <title>World of Tanks Project Orion 0.6.5 - Регистрация</title>
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
        <li class="nav-item"><a href="register.php" class="nav-link active">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
    </ul>
</div>

<div class="main-layout flex-col items-center" style="justify-content: center;">
    <div class="content-area w-full" style="max-width: 550px;">
        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Регистрация нового игрока</div>
            </div>
            <div class="card-body">
                <?php if (!empty($error)): ?>
                    <div class="alert alert-danger"><?php echo htmlspecialchars($error); ?></div>
                <?php endif; ?>
                
                <?php if (!empty($success)): ?>
                    <div class="alert alert-success"><?php echo htmlspecialchars($success); ?></div>
                    <div style="text-align: center; margin-top: 15px;">
                        <a href="login.php" class="btn btn-primary">Перейти ко входу</a>
                    </div>
                <?php else: ?>
                    <form action="register.php" method="POST">
                        <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token'] ?? ''); ?>">
                        <div class="form-group">
                            <label for="username">Никнейм</label>
                            <input type="text" name="username" id="username" class="form-control" placeholder="Введите никнейм..." required minlength="3" maxlength="24" autocomplete="username">
                        </div>
                        <div class="form-group">
                            <label for="email">Электронная почта (email)</label>
                            <input type="email" name="email" id="email" class="form-control" placeholder="Введите email..." required autocomplete="email">
                        </div>
                        <div class="form-group">
                            <label for="password">Пароль</label>
                            <input type="password" name="password" id="password" class="form-control" placeholder="Введите пароль..." required minlength="6" maxlength="128">
                        </div>
                        <div class="form-group">
                            <label for="password_confirm">Подтвердите пароль</label>
                            <input type="password" name="password_confirm" id="password_confirm" class="form-control" placeholder="Повторите пароль..." required minlength="6" maxlength="128">
                        </div>
                        <div class="form-group">
                            <label>Подтвердите, что вы не робот</label>
                            <div class="g-recaptcha" data-sitekey="<?php echo htmlspecialchars(RECAPTCHA_SITE_KEY, ENT_QUOTES, 'UTF-8'); ?>"></div>
                        </div>
                        <div class="form-actions">
                            <a href="login.php" style="font-size: 13px;">Уже зарегистрированы? Войти</a>
                            <button type="submit" class="btn btn-primary">Зарегистрироваться</button>
                        </div>
                    </form>
                <?php endif; ?>
            </div>
        </div>
    </div>
</div>

<div class="footer">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

</body>
</html>