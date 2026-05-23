<?php
require_once 'db.php';

if (isset($_SESSION['user_id'])) {
    header('Location: profile.php');
    exit;
}

$error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $username = trim($_POST['username'] ?? '');
    $password = $_POST['password'] ?? '';

    if (empty($username) || empty($password)) {
        $error = 'Пожалуйста, заполните все поля.';
    } else {
        try {
            $stmt = $pdo->prepare("SELECT id, username, password_hash, is_admin FROM accounts WHERE username = ? OR normalized_name = ? OR email = ?");
            $stmt->execute([$username, $username, $username]);
            $user = $stmt->fetch();

            if ($user && hash('sha256', $password) === $user['password_hash']) {
                $_SESSION['user_id'] = $user['id'];
                $_SESSION['username'] = $user['username'];
                $_SESSION['is_admin'] = (intval($user['is_admin']) === 1);

                $now = date('Y-m-d H:i:s');
                $update_stmt = $pdo->prepare("UPDATE accounts SET last_login = ? WHERE id = ?");
                $update_stmt->execute([$now, $user['id']]);

                header('Location: profile.php');
                exit;
            } else {
                $error = 'Неверное имя пользователя или пароль.';
            }
        } catch (Exception $e) {
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
    <title>World of Tanks Project Orion 0.6.5 - Вход</title>
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
        <li class="nav-item"><a href="register.php" class="nav-link">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
    </ul>
</div>

<div class="main-layout" style="justify-content: center;">
    <div class="content-area" style="width: 500px;">
        <div class="card">
            <div class="card-header">
                <div class="card-title">Авторизация</div>
            </div>
            <div class="card-body">
                <?php if (!empty($error)): ?>
                    <div class="alert alert-danger"><?php echo htmlspecialchars($error); ?></div>
                <?php endif; ?>
                
                <form action="login.php" method="POST">
                    <div class="form-group">
                        <label for="username">Логин или Email</label>
                        <input type="text" name="username" id="username" class="form-control" placeholder="Введите логин..." required autocomplete="off">
                    </div>
                    <div class="form-group">
                        <label for="password">Пароль</label>
                        <input type="password" name="password" id="password" class="form-control" placeholder="Введите пароль..." required>
                    </div>
                    <div style="margin-top: 25px; display: flex; justify-content: space-between; align-items: center;">
                        <a href="register.php" style="font-size: 13px;">Нет аккаунта? Создать</a>
                        <button type="submit" class="btn btn-primary">Войти</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="footer">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
</div>

</body>
</html>
