<?php
/**
 * Проста математична капча (без залежності від зовнішніх сервісів)
 */

session_start();

/**
 * Згенерувати нову капчу та повернути HTML-зображення
 * Викликати як: <img src="captcha.php?g=1">
 */
$gen = isset($_GET['g']);

if ($gen) {
    $num1 = rand(1, 20);
    $num2 = rand(1, 10);
    $ops = ['+', '-'];
    $op = $ops[array_rand($ops)];
    
    // Для віднімання гарантуємо невід'ємний результат
    if ($op === '-' && $num1 < $num2) {
        [$num1, $num2] = [$num2, $num1];
    }
    
    switch ($op) {
        case '+': $result = $num1 + $num2; break;
        case '-': $result = $num1 - $num2; break;
    }
    
    $_SESSION['captcha_result'] = $result;
    $_SESSION['captcha_ts'] = time();

    // Малюємо зображення
    $text = "$num1 $op $num2 = ?";
    $width = 200;
    $height = 60;
    
    $img = imagecreatetruecolor($width, $height);
    $bg = imagecolorallocate($img, 245, 245, 245);
    $text_color = imagecolorallocate($img, 50, 50, 50);
    $line_color = imagecolorallocate($img, 180, 180, 180);
    $noise_color = imagecolorallocate($img, 200, 200, 200);
    
    imagefilledrectangle($img, 0, 0, $width, $height, $bg);
    
    // Лінії для перешкод
    for ($i = 0; $i < 5; $i++) {
        imageline($img, rand(0, $width), rand(0, $height), rand(0, $width), rand(0, $height), $line_color);
    }
    
    // Крапки для перешкод
    for ($i = 0; $i < 50; $i++) {
        imagesetpixel($img, rand(0, $width), rand(0, $height), $noise_color);
    }
    
    $font_size = 5;
    $text_width = imagefontwidth($font_size) * strlen($text);
    $text_height = imagefontheight($font_size);
    $x = ($width - $text_width) / 2;
    $y = ($height - $text_height) / 2;
    
    imagestring($img, $font_size, $x, $y, $text, $text_color);
    
    header('Content-Type: image/png');
    header('Cache-Control: no-cache, no-store, must-revalidate');
    imagepng($img);
    imagedestroy($img);
    exit;
}

/**
 * Перевірити відповідь капчі
 * @param string $answer Відповідь користувача
 * @return bool
 */
function verify_captcha($answer) {
    if (!isset($_SESSION['captcha_result']) || !isset($_SESSION['captcha_ts'])) {
        return false;
    }
    
    // Капча дійсна 5 хвилин
    if (time() - $_SESSION['captcha_ts'] > 300) {
        unset($_SESSION['captcha_result'], $_SESSION['captcha_ts']);
        return false;
    }
    
    $expected = (int)$_SESSION['captcha_result'];
    $user_answer = (int)trim($answer);
    
    // Очищаємо щоб не можна було використати двічі
    unset($_SESSION['captcha_result'], $_SESSION['captcha_ts']);
    
    return $expected === $user_answer;
}