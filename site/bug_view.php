<?php
require_once 'db.php';
require_once 'recaptcha.php';

$error = '';
$success = '';

$bug_id = isset($_GET['id']) ? intval($_GET['id']) : 0;

if (!$bug_id) {
    header("Location: bugs.php");
    exit;
}

// Handle POST actions
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action'])) {
    if (!isset($_SESSION['user_id'])) {
        $error = 'Вы должны войти в систему для этого действия.';
    } elseif (!verify_csrf($_POST['csrf_token'] ?? '')) {
        $error = 'Ошибка CSRF. Попробуйте еще раз.';
    } else {
        if ($_POST['action'] === 'add_comment') {
            $stmt = $pdo->prepare("SELECT created_at FROM bug_comments WHERE account_id = ? ORDER BY created_at DESC LIMIT 1");
            $stmt->execute([$_SESSION['user_id']]);
            $last_comment = $stmt->fetchColumn();

            if ($last_comment && time() - strtotime($last_comment) < 60 && (!isset($_SESSION['is_admin']) || !$_SESSION['is_admin'])) {
                $error = 'Анти-спам: вы можете оставлять комментарии не чаще, чем раз в минуту. Пожалуйста, подождите.';
            } elseif (!verify_recaptcha($_POST['g-recaptcha-response'] ?? '')) {
                $error = 'Пожалуйста, подтвердите, что вы не робот (reCAPTCHA).';
            } else {
                $comment = trim($_POST['comment'] ?? '');
                $comment = trim($_POST['comment'] ?? '');
                if (mb_strlen($comment) < 2) {
                    $error = 'Комментарий слишком короткий.';
                } else {
                    try {
                        $stmt = $pdo->prepare("INSERT INTO bug_comments (bug_id, account_id, comment) VALUES (?, ?, ?)");
                        $stmt->execute([$bug_id, $_SESSION['user_id'], $comment]);
                        $success = 'Комментарий добавлен.';
                    } catch (Exception $e) {
                        error_log("Add comment error: " . $e->getMessage());
                        $error = 'Ошибка при добавлении комментария.';
                    }
                }
            }
        } elseif ($_POST['action'] === 'change_status' && isset($_SESSION['is_admin']) && $_SESSION['is_admin']) {
            $status = $_POST['status'] ?? 'open';
            $valid_statuses = ['open', 'in_progress', 'resolved', 'closed'];
            if (in_array($status, $valid_statuses)) {
                try {
                    $stmt = $pdo->prepare("UPDATE bug_reports SET status = ? WHERE id = ?");
                    $stmt->execute([$status, $bug_id]);
                    $success = 'Статус обновлен.';
                } catch (Exception $e) {
                    error_log("Change status error: " . $e->getMessage());
                    $error = 'Ошибка при обновлении статуса.';
                }
            }
        } elseif ($_POST['action'] === 'approve_bug' && isset($_SESSION['is_admin']) && $_SESSION['is_admin']) {
            $pdo->prepare("UPDATE bug_reports SET is_approved = 1 WHERE id = ?")->execute([$bug_id]);
            $success = 'Репорт успешно одобрен!';
        } elseif ($_POST['action'] === 'delete_bug' && isset($_SESSION['is_admin']) && $_SESSION['is_admin']) {
            $pdo->prepare("DELETE FROM bug_comments WHERE bug_id = ?")->execute([$bug_id]);
            $pdo->prepare("DELETE FROM bug_reports WHERE id = ?")->execute([$bug_id]);
            header("Location: bugs.php");
            exit;
        }
    }
}

// Fetch bug details
$bug = null;
try {
    $stmt = $pdo->prepare("
        SELECT b.*, a.username, a.is_admin 
        FROM bug_reports b 
        LEFT JOIN accounts a ON b.account_id = a.id 
        WHERE b.id = ?
    ");
    $stmt->execute([$bug_id]);
    $bug = $stmt->fetch();
} catch (Exception $e) {
    error_log("Fetch bug error: " . $e->getMessage());
}

if (!$bug) {
    die("Баг-репорт не найден.");
}

$is_admin = isset($_SESSION['is_admin']) && $_SESSION['is_admin'];
$is_approved = intval($bug['is_approved'] ?? 0) === 1;

if (!$is_approved && !$is_admin) {
    die("<div style='padding:50px; text-align:center; color:#fff; font-family:sans-serif; background:#09090a; height:100vh;'><h2>Доступ закрыт</h2><p style='color:#8c8c8c; margin-bottom:20px;'>Этот баг-репорт ожидает проверки администратором.</p><a href='bugs.php' style='color:#e5a93b;'>Вернуться назад</a></div>");
}

// Fetch comments
$comments = [];
try {
    $stmt = $pdo->prepare("
        SELECT c.*, a.username, a.is_admin 
        FROM bug_comments c 
        LEFT JOIN accounts a ON c.account_id = a.id 
        WHERE c.bug_id = ? 
        ORDER BY c.created_at ASC
    ");
    $stmt->execute([$bug_id]);
    $comments = $stmt->fetchAll();
} catch (Exception $e) {
    error_log("Fetch comments error: " . $e->getMessage());
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
    <title>World of Tanks Project Orion 0.6.5 - Баг-репорт #<?php echo $bug_id; ?></title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config={important:true,theme:{extend:{colors:{wot:{gold:'#e5a93b',dark:'#1a1a1c',panel:'#101011'}}}}}</script>
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>
    <style>
        .comment-box {
            padding: 15px;
            border-bottom: 1px solid #28282a;
        }
        .comment-box:last-child {
            border-bottom: none;
        }
        .comment-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 12px;
            color: #8c8c8c;
        }
        .comment-author {
            font-weight: 600;
            color: #e5a93b;
        }
        .admin-badge {
            background: #ff4500;
            color: white;
            padding: 1px 5px;
            border-radius: 3px;
            font-size: 10px;
            text-transform: uppercase;
            margin-left: 5px;
        }
        .comment-body {
            color: #cccccc;
            white-space: pre-wrap;
        }
        .bug-status-banner {
            padding: 10px 15px;
            background: #111112;
            border-bottom: 1px solid #28282a;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
    </style>
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content flex-col md:flex-row md:justify-between text-center md:text-left gap-1 md:gap-0">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Скачать</a>
            <a href="bugs.php">← Назад к багам</a>
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

<div class="main-layout" style="width: 100%; align-items: flex-start;">
    <div class="content-area" style="width: calc(70% - 15px); flex-shrink: 0;">
        
        <?php if ($error): ?>
            <div class="alert alert-danger"><?php echo htmlspecialchars($error); ?></div>
        <?php endif; ?>
        <?php if ($success): ?>
            <div class="alert alert-success"><?php echo htmlspecialchars($success); ?></div>
        <?php endif; ?>

        <div class="card">
            <div class="bug-status-banner">
                <div>Статус: <?php echo get_status_label($bug['status']); ?></div>
                <div style="font-size: 12px; color: #8c8c8c;">ID: #<?php echo $bug['id']; ?></div>
            </div>
            <div class="card-header" style="border-top: 1px solid #28282a;">
                <div class="card-title text-sm md:text-lg"><?php echo htmlspecialchars($bug['title']); ?></div>
            </div>
            <div class="card-body">
                <div class="comment-header">
                    <div>
                        <span class="comment-author"><?php echo htmlspecialchars($bug['username'] ?? 'Неизвестно'); ?></span>
                        <?php if ($bug['is_admin']) echo '<span class="admin-badge">Admin</span>'; ?>
                    </div>
                    <div><?php echo htmlspecialchars($bug['created_at']); ?></div>
                </div>
                <div class="comment-body" style="font-size: 15px; margin-top: 10px;"><?php echo htmlspecialchars($bug['description']); ?></div>
            </div>
        </div>

        <h3 style="color: #e5a93b; font-size: 18px; font-weight: 600; margin-top: 20px; text-transform: uppercase;">Комментарии (<?php echo count($comments); ?>)</h3>

        <div class="card" style="margin-top: 15px;">
            <div class="card-body" style="padding: 0;">
                <?php if (empty($comments)): ?>
                    <div style="padding: 20px; text-align: center; color: #8c8c8c;">Нет комментариев.</div>
                <?php else: ?>
                    <?php foreach ($comments as $c): ?>
                        <div class="comment-box">
                            <div class="comment-header">
                                <div>
                                    <span class="comment-author"><?php echo htmlspecialchars($c['username'] ?? 'Неизвестно'); ?></span>
                                    <?php if ($c['is_admin']) echo '<span class="admin-badge">Admin</span>'; ?>
                                </div>
                                <div><?php echo htmlspecialchars($c['created_at']); ?></div>
                            </div>
                            <div class="comment-body"><?php echo htmlspecialchars($c['comment']); ?></div>
                        </div>
                    <?php endforeach; ?>
                <?php endif; ?>
            </div>
        </div>

        <?php if (isset($_SESSION['user_id'])): ?>
            <div class="card" style="margin-top: 30px;">
                <div class="card-header">
                    <div class="card-title text-sm md:text-md">Добавить комментарий</div>
                </div>
                <div class="card-body">
                    <form method="POST" action="bug_view.php?id=<?php echo $bug_id; ?>">
                        <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                        <input type="hidden" name="action" value="add_comment">
                        <div class="form-group">
                            <textarea name="comment" class="form-control" rows="3" required placeholder="Ваш ответ..."></textarea>
                        </div>
                        <div class="form-group" style="margin-bottom: 20px;">
                            <div class="g-recaptcha" data-sitekey="<?php echo htmlspecialchars(RECAPTCHA_SITE_KEY, ENT_QUOTES, 'UTF-8'); ?>"></div>
                        </div>
                        <button type="submit" class="btn btn-primary">Отправить</button>
                    </form>
                </div>
            </div>
        <?php else: ?>
            <div style="margin-top: 20px; padding: 15px; background: rgba(14, 14, 15, 0.95); border: 1px solid #28282a; text-align: center; border-radius: 4px;">
                <a href="login.php" style="color: #e5a93b;">Войдите</a>, чтобы оставить комментарий.
            </div>
        <?php endif; ?>

    </div>

    <div class="sidebar-area" style="width: calc(30% - 15px); flex-shrink: 0;">
        <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
            <div class="card">
                <div class="card-header">
                    <div class="card-title text-sm md:text-lg">Управление</div>
                </div>
                <div class="card-body">
                    <form method="POST" action="bug_view.php?id=<?php echo $bug_id; ?>">
                        <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                        <input type="hidden" name="action" value="change_status">
                        <div class="form-group">
                            <label>Статус</label>
                            <select name="status" class="form-control">
                                <option value="open" <?php echo $bug['status'] === 'open' ? 'selected' : ''; ?>>Открыт</option>
                                <option value="in_progress" <?php echo $bug['status'] === 'in_progress' ? 'selected' : ''; ?>>В работе</option>
                                <option value="resolved" <?php echo $bug['status'] === 'resolved' ? 'selected' : ''; ?>>Исправлено</option>
                                <option value="closed" <?php echo $bug['status'] === 'closed' ? 'selected' : ''; ?>>Закрыт</option>
                            </select>
                        </div>
                        <button type="submit" class="btn btn-secondary" style="width: 100%; margin-bottom: 15px;">Сохранить статус</button>
                    </form>

                    <?php if (!$is_approved): ?>
                        <form method="POST" onsubmit="return confirm('Одобрить этот репорт?');" style="margin-bottom: 10px;">
                            <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                            <input type="hidden" name="action" value="approve_bug">
                            <button type="submit" class="btn btn-primary" style="width: 100%; background: #27ae60;">Одобрить (Сделать публичным)</button>
                        </form>
                    <?php endif; ?>
                    <form method="POST" onsubmit="return confirm('Точно удалить этот репорт?');">
                        <input type="hidden" name="csrf_token" value="<?php echo htmlspecialchars($_SESSION['csrf_token']); ?>">
                        <input type="hidden" name="action" value="delete_bug">
                        <button type="submit" class="btn btn-primary" style="width: 100%; background: #c0392b;">Удалить репорт</button>
                    </form>
                </div>
            </div>
        <?php endif; ?>
    </div>
</div>

<div class="footer text-[11px] md:text-xs px-3 md:px-0">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<script src="sparks.js"></script>
</body>
</html>
