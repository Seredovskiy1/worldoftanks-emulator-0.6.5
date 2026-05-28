document.addEventListener('DOMContentLoaded', () => {
    // Check for reduced motion preference
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return;
    }

    // Create canvas
    const canvas = document.createElement('canvas');
    canvas.id = 'sparksFx';
    canvas.style.position = 'fixed';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.width = '100vw';
    canvas.style.height = '100vh';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '0'; // Behind everything
    
    // Add to body
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    let width, height;
    let particles = [];

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(width * ratio);
        canvas.height = Math.floor(height * ratio);
        ctx.scale(ratio, ratio);
    }

    function spawn() {
        // Spawn from bottom
        particles.push({
            x: Math.random() * width,
            y: height + 20,
            vx: -1.5 + Math.random() * 3, // Drift left/right
            vy: -1 - Math.random() * 4, // Speed up
            life: 120 + Math.random() * 150, // How long it lives
            age: 0,
            size: 1 + Math.random() * 2.5,
            hue: 20 + Math.random() * 35, // Gold/Orange sparks (20 to 55 hue)
            alpha: Math.random() * 0.7 + 0.3
        });
    }

    let frame = 0;
    function draw() {
        frame++;
        ctx.clearRect(0, 0, width, height);
        ctx.globalCompositeOperation = 'lighter';
        
        // Spawn rate
        if (frame % 2 === 0 && particles.length < 180) {
            spawn();
            if (Math.random() > 0.5) spawn(); // Occasionally spawn two
        }

        for (let i = particles.length - 1; i >= 0; i--) {
            const p = particles[i];
            p.age++;
            p.x += p.vx;
            p.y += p.vy;
            
            // Add some wobble and drift
            p.vx += (Math.random() - 0.5) * 0.15;
            
            // Fade out based on life
            const progress = p.age / p.life;
            const currentAlpha = p.alpha * (1 - Math.pow(progress, 2));

            ctx.fillStyle = `hsla(${p.hue}, 100%, 60%, ${currentAlpha})`;
            ctx.shadowColor = `hsla(${p.hue}, 100%, 50%, ${currentAlpha * 0.8})`;
            ctx.shadowBlur = p.size * 4;

            ctx.beginPath();
            ctx.arc(p.x, p.y, p.size * (1 - progress * 0.5), 0, Math.PI * 2);
            ctx.fill();

            // Random flash or "pop"
            if (Math.random() < 0.005) {
                ctx.fillStyle = `hsla(60, 100%, 90%, ${currentAlpha})`;
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.size * 2, 0, Math.PI * 2);
                ctx.fill();
            }

            if (p.age >= p.life || p.y < -50 || p.x < -50 || p.x > width + 50) {
                particles.splice(i, 1);
            }
        }
        ctx.globalCompositeOperation = 'source-over';
        window.requestAnimationFrame(draw);
    }

    window.addEventListener('resize', resize);
    resize();
    draw();
});
