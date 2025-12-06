function toggleVisibility(fieldId) {
    const input = document.getElementById(fieldId);
    const icon = document.getElementById('icon-' + fieldId);
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    }
}

function copyUrl() {
    const urlInput = document.getElementById('webhook-url');
    const textToCopy = urlInput.value;
    const icon = document.getElementById('copy-icon');

    const showSuccess = () => {
        icon.classList.remove('fa-copy');
        icon.classList.add('fa-check');
        setTimeout(() => {
            icon.classList.remove('fa-check');
            icon.classList.add('fa-copy');
        }, 2000);
    };

    // Modern, secure context method
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(textToCopy).then(showSuccess).catch(err => {
            console.error('Could not copy text: ', err);
        });
    } else {
        // Fallback for older browsers or insecure contexts
        const textArea = document.createElement("textarea");
        textArea.value = textToCopy;
        
        // Make sure it's not visible
        textArea.style.position = "fixed";
        textArea.style.top = "-9999px";
        textArea.style.left = "-9999px";

        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();

        try {
            document.execCommand('copy');
            showSuccess();
        } catch (err) {
            console.error('Fallback copy failed: ', err);
        } finally {
            document.body.removeChild(textArea);
        }
    }
}

window.addEventListener('DOMContentLoaded', (event) => {
    const urlInput = document.getElementById('webhook-url');
    if (urlInput) {
        // Use the config object from the global scope set by Flask
        const baseUrl = window.appConfig.base_url || `${window.location.protocol}//${window.location.host}`;
        const apiKey = window.appConfig.security_api_key;
        urlInput.value = `${baseUrl}/webhook/${apiKey}`;
    }
    
    // Initialize all library toggles
    const libraryNames = window.appConfig.library_keys;
    libraryNames.forEach(name => onScanToggleChange(name));
});

function onScanToggleChange(name) {
    const scanToggle = document.getElementById(`scan-toggle-${name}`);
    const notifyGroup = document.getElementById(`notify-group-${name}`);
    const notifyToggle = document.getElementById(`notify-toggle-${name}`);

    if (scanToggle.checked) {
        notifyToggle.disabled = false;
        notifyGroup.classList.remove('opacity-50');
    } else {
        notifyToggle.disabled = true;
        notifyGroup.classList.add('opacity-50');
    }
}