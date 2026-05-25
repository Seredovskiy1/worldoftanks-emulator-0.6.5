<?php
/**
 * reCAPTCHA verification helper (v2 "I'm not a robot")
 */
require_once __DIR__ . '/recaptcha_config.php';

/**
 * Verify Google reCAPTCHA v2 response.
 *
 * @param string $response The 'g-recaptcha-response' value from the form.
 * @return bool True if verification passes.
 */
function verify_recaptcha($response) {
    $response = trim((string)$response);
    if ($response === '') {
        return false;
    }

    $post_fields = http_build_query([
        'secret'   => RECAPTCHA_SECRET_KEY,
        'response' => $response,
        'remoteip' => $_SERVER['REMOTE_ADDR'] ?? '',
    ]);

    $result = false;

    if (function_exists('curl_init')) {
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => 'https://www.google.com/recaptcha/api/siteverify',
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $post_fields,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 10,
            CURLOPT_SSL_VERIFYPEER => true,
        ]);
        $result = curl_exec($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
    } else {
        $context = stream_context_create([
            'http' => [
                'method'  => 'POST',
                'header'  => "Content-Type: application/x-www-form-urlencoded\r\n",
                'content' => $post_fields,
                'timeout' => 10,
            ],
        ]);
        $result = @file_get_contents('https://www.google.com/recaptcha/api/siteverify', false, $context);
        $http_code = 200;
    }

    if ($result === false) {
        return false;
    }

    $data = json_decode($result, true);
    if (!is_array($data)) {
        return false;
    }

    // reCAPTCHA v2: просто перевіряємо success
    return !empty($data['success']);
}