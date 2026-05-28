<?php
require_once 'db.php';
require_once 'recaptcha.php';

$error = '';
$success = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action'])) {
    if (!verify_csrf($_POST['csrf_token'] ?? '')) {
        $error = 'Ошибка CSRF. Попробуйте еще раз.';
    } else {
        $action = $_POST['action'];
        
        if ($action === 'create_bug') {
            if (!isset($_SESSION['user_id'])) {
                $error = 'Вы должны войти в систему, чтобы создать баг-репорт.';
            } else {
                $stmt = $pdo->prepare("SELECT is_banned_reports FROM accounts WHERE id = ?");
                $stmt->execute([$_SESSION['user_id']]);
                $is_banned = intval($stmt->fetchColumn()) === 1;

                $stmt = $pdo->prepare("SELECT TIMESTAMPDIFF(SECOND, MAX(created_at), NOW()) FROM bug_reports WHERE account_id = ?");
                $stmt->execute([$_SESSION['user_id']]);
                $seconds_since_last = $stmt->fetchColumn();
                $recent_bugs = ($seconds_since_last !== null && $seconds_since_last !== false && intval($seconds_since_last) < 3600) ? 1 : 0;

                if ($is_banned && (!isset($_SESSION['is_admin']) || !$_SESSION['is_admin'])) {
                    $error = 'Ваш аккаунт заблокирован для создания баг-репортов администрацией.';
                } elseif ($recent_bugs > 0 && (!isset($_SESSION['is_admin']) || !$_SESSION['is_admin'])) {
                    $error = 'Анти-спам: вы можете создавать баг-репорты не чаще, чем раз в час. Пожалуйста, подождите.';
                } elseif (!verify_recaptcha($_POST['g-recaptcha-response'] ?? '')) {
                    $error = 'Пожалуйста, подтвердите, что вы не робот (reCAPTCHA).';
                } else {
                    $title = trim($_POST['title'] ?? '');
                    $description = trim($_POST['description'] ?? '');

                    if (mb_strlen($title) < 5 || mb_strlen($title) > 100) {
                        $error = 'Заголовок должен содержать от 5 до 100 символов.';
                    } elseif (mb_strlen($description) < 10) {
                        $error = 'Описание должно содержать минимум 10 символов.';
                    } else {
                        try {
                            $stmt = $pdo->prepare("INSERT INTO bug_reports (account_id, title, description, status, is_approved) VALUES (?, ?, ?, 'open', 0)");
                            $stmt->execute([$_SESSION['user_id'], $title, $description]);
                            $success = 'Баг-репорт успешно отправлен. Он появится на сайте после проверки администратором.';
                        } catch (Exception $e) {
                            error_log("Create bug error: " . $e->getMessage());
                            $error = 'Произошла ошибка при создании баг-репорта.';
                        }
                    }
                }
            }
        } elseif ($action === 'approve_bug' && isset($_SESSION['is_admin']) && $_SESSION['is_admin']) {
            $bug_id = intval($_POST['bug_id'] ?? 0);
            $pdo->prepare("UPDATE bug_reports SET is_approved = 1 WHERE id = ?")->execute([$bug_id]);
            $success = 'Репорт успешно одобрен!';
        } elseif ($action === 'delete_bug' && isset($_SESSION['is_admin']) && $_SESSION['is_admin']) {
            $bug_id = intval($_POST['bug_id'] ?? 0);
            $pdo->prepare("DELETE FROM bug_comments WHERE bug_id = ?")->execute([$bug_id]);
            $pdo->prepare("DELETE FROM bug_reports WHERE id = ?")->execute([$bug_id]);
            $success = 'Репорт был удален!';
        }
    }
}

// Fetch bugs
$bugs = [];
try {
    $stmt = $pdo->query("
        SELECT b.*, a.username 
        FROM bug_reports b 
        LEFT JOIN accounts a ON b.account_id = a.id 
        ORDER BY b.created_at DESC
    ");
    $bugs = $stmt->fetchAll();
} catch (Exception $e) {
    error_log("Fetch bugs error: " . $e->getMessage());
}

$active_page = 'bugs';

function get_status_label($status) {
    switch ($status) {
        case 'open': return '<span style="color: #e74c3c; font-weight: bold;">Открыт</span>';
        case 'in_progress': return '<span style="color: #f39c12; font-weight: bold;">В работе</span>';
        case 'resolved': return '<span style="color: #2ecc71; font-weight: bold;">Исправлено</span>';
        case 'closed': return '<span style="color: #95a5a6; font-weight: bold;">Закрыт</span>';
        default: return $status;
    }
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Баг-репорты</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config={important:true,theme:{extend:{colors:{wot:{gold:'#e5a93b',dark:'#1a1a1c',panel:'#101011'}}}}}</script>
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>
    <style>
        .bug-item {
            display: flex;
            flex-direction: column;
            padding: 15px;
            border-bottom: 1px solid #28282a;
            transition: background 0.2s;
        }
        .bug-item:hover {
            background: rgba(255, 255, 255, 0.03);
        }
        .bug-item:last-child {
            border-bottom: none;
        }
        .bug-title {
            font-size: 18px;
            font-weight: 600;
            color: #e5a93b;
            text-decoration: none;
            margin-bottom: 5px;
        }
        .bug-title:hover {
            color: #ff4500;
        }
        .bug-meta {
            font-size: 12px;
            color: #8c8c8c;
            display: flex;
            gap: 15px;
        }
        .status-badge {
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            text-transform: uppercase;
            background: #1a1a1c;
            border: 1px solid #333;
        }
    </style>
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content flex-col md:flex-row md:justify-between text-center md:text-left gap-1 md:gap-0">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Скачать</a>
        </div>
        <div class="top-bar-auth">
            <?php if (isset($_SESSION['user_id'])): ?>
                <span>Здравствуйте, <a href="profile.php"><?php echo htmlspecialchars($_SESSION['username']); ?></a></span>
                <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
                    | <a href="admin.php">Админ-панель</a>
                <?php endif; ?>
                | <a href="logout.php" class="logout">Выйти</a>
            <?php else: ?>
                <a href="login.php">Войти</a> или <a href="register.php">Создать аккаунт</a>
            <?php endif; ?>
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
        <li class="nav-item"><a href="bugs.php" class="nav-link active">Баг-репорты</a></li>
        <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
            <li class="nav-item"><a href="admin.php" class="nav-link">Управление танками</a></li>
        <?php endif; ?>
    </ul>
</div>

<div class="main-layout" style="width: 100%; align-items: flex-start;">
    <div class="content-area" style="width: calc(70% - 15px); flex-shrink: 0;">
        
        <?php if ($error): ?>
            <div class="alert alert-danger"><?php echo htmlspecialchars($error); ?></div>
        <?php endif; ?>
        <?php if ($success): ?>
            <div class="alert alert-success"><?php echo htmlspecialchars($success); ?></div>
        <?php endif; ?>

        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Форум Баг-репортов</div>
            </div>
            <div class="card-body" style="padding: 0;">
                <?php if (empty($bugs)): ?>
                    <div style="padding: 20px; text-align: center; color: #8c8c8c;">Баг-репортов пока нет.</div>
                <?php else: ?>
                    <?php foreach ($bugs as $bug): ?>
                        <?php
                        $is_admin = isset($_SESSION['is_admin']) && $_SESSION['is_admin'];
                        $is_approved = intval($bug['is_approved']) === 1;
                        ?>
                        <div class="bug-item">
                            <?php if ($is_approved || $is_admin): ?>
                                <a href="bug_view.php?id=<?php echo $bug['id']; ?>" class="bug-title"><?php echo htmlspecialchars($bug['title']); ?></a>
                            <?php else: ?>
                                <span class="bug-title" style="color: #8c8c8c; cursor: default;">Репорт #<?php echo $bug['id']; ?> - Ожидает проверки</span>
                            <?php endif; ?>
                            
                            <div class="bug-meta" style="align-items: center;">
                                <span class="status-badge"><?php echo get_status_label($bug['status']); ?></span>
                                <span>Автор: <?php echo htmlspecialchars($bug['username'] ?? 'Неизвестно'); ?></span>
                                <span>Создано: <?php echo htmlspecialchars($bug['created_at']); ?></span>
                                
                                <?php if ($is_admin): ?>
                                    <div style="margin-left: auto; display: flex; gap: 10px;">
                                        <?php if (!$is_approved): ?>
                                            <form method="POST" style="margin: 0;" onsubmit="return confirm('Одобрить этот репорт? Он станет виден всем.');">
                                                <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                                                <input type="hidden" name="action" value="approve_bug">
                                                <input type="hidden" name="bug_id" value="<?php echo $bug['id']; ?>">
                                                <button type="submit" class="status-badge" style="background: #27ae60; color: white; border:none; cursor:pointer;">Одобрить</button>
                                            </form>
                                        <?php endif; ?>
                                        <form method="POST" style="margin: 0;" onsubmit="return confirm('Точно удалить этот репорт навсегда?');">
                                            <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                                            <input type="hidden" name="action" value="delete_bug">
                                            <input type="hidden" name="bug_id" value="<?php echo $bug['id']; ?>">
                                            <button type="submit" class="status-badge" style="background: #c0392b; color: white; border:none; cursor:pointer;">Удалить</button>
                                        </form>
                                    </div>
                                <?php endif; ?>
                            </div>
                        </div>
                    <?php endforeach; ?>
                <?php endif; ?>
            </div>
        </div>
    </div>

    <div class="sidebar-area" style="width: calc(30% - 15px); flex-shrink: 0;">
        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Создать репорт</div>
            </div>
            <div class="card-body">
                <?php if (isset($_SESSION['user_id'])): ?>
                    <form method="POST" action="bugs.php" onsubmit="this.querySelector('button[type=submit]').disabled=true; this.querySelector('button[type=submit]').innerHTML='Отправка...';">
                        <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                        <input type="hidden" name="action" value="create_bug">
                        
                        <div class="form-group">
                            <label>Заголовок (кратко о проблеме)</label>
                            <input type="text" name="title" class="form-control" required minlength="5" maxlength="100">
                        </div>
                        
                        <div class="form-group">
                            <label>Подробное описание</label>
                            <textarea name="description" class="form-control" rows="5" required minlength="10" placeholder="Опишите, как воспроизвести баг..."></textarea>
                        </div>
                        
                        <div class="form-group" style="margin-bottom: 20px;">
                            <div class="g-recaptcha" data-sitekey="<?php echo htmlspecialchars(RECAPTCHA_SITE_KEY, ENT_QUOTES, 'UTF-8'); ?>"></div>
                        </div>
                        
                        <button type="submit" class="btn btn-primary" style="width: 100%;">Отправить</button>
                    </form>
                <?php else: ?>
                    <p style="font-size: 13px; color: #aaaaaa; margin-bottom: 15px;">Только авторизованные пользователи могут создавать баг-репорты.</p>
                    <a href="login.php" class="btn btn-secondary" style="width: 100%; text-align: center;">Войти</a>
                <?php endif; ?>
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
