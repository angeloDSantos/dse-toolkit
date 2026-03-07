// DSE Toolkit — Frontend JS

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
    const flashes = document.querySelectorAll('.flash');
    flashes.forEach(f => {
        setTimeout(() => {
            f.style.opacity = '0';
            f.style.transform = 'translateY(-8px)';
            setTimeout(() => f.remove(), 300);
        }, 5000);
    });

    // Poll tool status every 10 seconds on outreach page
    if (window.location.pathname === '/outreach') {
        setInterval(pollToolStatus, 10000);
    }
});

async function pollToolStatus() {
    try {
        const resp = await fetch('/api/tool-status');
        const data = await resp.json();
        // Update badges (light refresh without full page reload)
        console.log('Tool status:', data);
    } catch (e) {
        console.warn('Status poll failed:', e);
    }
}
