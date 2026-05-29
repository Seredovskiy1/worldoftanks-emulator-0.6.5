document.addEventListener('DOMContentLoaded', () => {
    // Check for reduced motion preference
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return;
    }

    // Disable particle FX entirely on mobile / small screens
    const isMobile = window.innerWidth < 768 || ('ontouchstart' in window);
    if (isMobile) {
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
    canvas.style.zIndex = '0';
    
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d', { alpha: true });
    let width, height;

    // Object pool — avoid GC churn from constant splice + push
    const MAX_PARTICLES = 60;
    const pool = new Array(MAX_PARTICLES);
    let poolSize = 0;

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
        // Cap pixel ratio at 1 for the FX canvas — no visual difference, huge perf gain
        canvas.width = width;
        canvas.height = height;
    }

    function spawn() {
        if (poolSize >= MAX_PARTICLES) return;
        pool[poolSize++] = {
            x: Math.random() * width,
            y: height + 20,
            vx: -1.5 + Math.random() * 3,
            vy: -1 - Math.random() * 3,
            life: 100 + Math.random() * 120,
            age: 0,
            size: 1 + Math.random() * 2,
            hue: 20 + Math.random() * 35,
            alpha: Math.random() * 0.6 + 0.3
        };
    }

    let frame = 0;
    let running = true;

    function draw() {
        if (!running) return;
        frame++;
        ctx.clearRect(0, 0, width, height);
        ctx.globalCompositeOperation = 'lighter';
        
        // Spawn every 4th frame instead of every 2nd — halves spawn rate
        if (frame % 4 === 0) {
            spawn();
        }

        let writeIdx = 0;
        for (let i = 0; i < poolSize; i++) {
            const p = pool[i];
            p.age++;
            p.x += p.vx;
            p.y += p.vy;
            
            // Simplified wobble
            p.vx += (Math.random() - 0.5) * 0.1;
            
            const progress = p.age / p.life;
            if (progress >= 1 || p.y < -30 || p.x < -30 || p.x > width + 30) {
                continue; // Dead — don't copy to output
            }

            const currentAlpha = p.alpha * (1 - progress * progress);

            // No shadowBlur — this is the #1 GPU killer on mobile/low-end
            ctx.fillStyle = `hsla(${p.hue}, 100%, 60%, ${currentAlpha})`;
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.size * (1 - progress * 0.5), 0, Math.PI * 2);
            ctx.fill();

            // Keep particle alive
            pool[writeIdx++] = p;
        }
        poolSize = writeIdx;

        ctx.globalCompositeOperation = 'source-over';
        requestAnimationFrame(draw);
    }

    // Pause when tab is hidden
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            running = false;
        } else {
            running = true;
            requestAnimationFrame(draw);
        }
    });

    window.addEventListener('resize', resize);
    resize();
    draw();
});
