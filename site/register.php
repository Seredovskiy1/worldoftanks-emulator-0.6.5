<?php
require_once 'db.php';

if (isset($_SESSION['user_id'])) {
    header('Location: profile.php');
    exit;
}

$error = '';
$success = '';

if (!function_exists('normalize_login_name')) {
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

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
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
    } elseif ($password !== $password_confirm) {
        $error = 'Пароли не совпадают.';
    } else {
        $normalized = normalize_login_name($username);

        try {
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM accounts WHERE normalized_name = ? OR username = ? OR email = ?");
            $stmt->execute([$normalized, $username, $email]);
            if ($stmt->fetchColumn() > 0) {
                $error = 'Этот никнейм или email уже зарегистрирован.';
            } else {
                $password_hash = hash('sha256', $password);
                $now = date('Y-m-d H:i:s');

                $pdo->beginTransaction();

                $stmt = $pdo->prepare("INSERT INTO accounts (username, email, normalized_name, password_hash, created_at, last_login) VALUES (?, ?, ?, ?, ?, ?)");
                $stmt->execute([$username, $email, $normalized, $password_hash, $now, $now]);
                
                $new_id = $pdo->lastInsertId();

                $stmt = $pdo->prepare("INSERT INTO dossier (account_id) VALUES (?)");
                $stmt->execute([$new_id]);

                $pdo->commit();

                $success = 'Регистрация успешна! Теперь вы можете войти.';
            }
        } catch (Exception $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            $error = 'Ошибка базы данных: ' . $e->getMessage();
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
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Скачать</a>
        </div>
        <div class="top-bar-auth">
            <a href="login.php">Войти</a> или <a href="register.php">Создать аккаунт</a>
        </div>
    </div>
</div>

<div class="header-banner">
    <div class="logo-container">
        <img src="images/logo.png" alt="Logo" class="logo-icon">
        <div class="logo-text-wrapper">
            <div class="logo-text">World of Tanks</div>
            <div class="logo-subtext">Project Orion 0.6.5</div>
        </div>
    </div>
</div>

<div class="nav-container">
    <ul class="nav-menu">
        <li class="nav-item"><a href="index.php" class="nav-link">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link">Играть</a></li>
        <li class="nav-item"><a href="register.php" class="nav-link active">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
    </ul>
</div>

<div class="main-layout" style="justify-content: center;">
    <div class="content-area" style="width: 550px;">
        <div class="card">
            <div class="card-header">
                <div class="card-title">Регистрация нового игрока</div>
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
                        <div class="form-group">
                            <label for="username">Никнейм</label>
                            <input type="text" name="username" id="username" class="form-control" placeholder="Введите никнейм..." required minlength="3" maxlength="24" autocomplete="off">
                        </div>
                        <div class="form-group">
                            <label for="email">Электронная почта (email)</label>
                            <input type="email" name="email" id="email" class="form-control" placeholder="Введите email..." required autocomplete="off">
                        </div>
                        <div class="form-group">
                            <label for="password">Пароль</label>
                            <input type="password" name="password" id="password" class="form-control" placeholder="Введите пароль..." required minlength="6">
                        </div>
                        <div class="form-group">
                            <label for="password_confirm">Подтвердите пароль</label>
                            <input type="password" name="password_confirm" id="password_confirm" class="form-control" placeholder="Повторите пароль..." required minlength="6">
                        </div>
                        <div style="margin-top: 25px; display: flex; justify-content: space-between; align-items: center;">
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
</div>

</body>
</html>
