// @ts-check
/**
 * Clipboard utilities with toast notifications
 * @typedef {{copy: (text: string, successMessage?: string) => Promise<boolean>, download: (content: string, filename: string, mimeType?: string) => void, exportCSV: (data: unknown[], filename: string) => void, toast: (message: string, type?: 'success' | 'error' | 'info') => void}} ClipboardUtils
 */
(function() {
    'use strict';

    /** @type {HTMLDivElement | null} */
    let toastContainer = null;

    /**
     * @returns {HTMLDivElement}
     */
    function getToastContainer() {
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'fixed bottom-4 right-4 z-50 flex flex-col gap-2';
            document.body.appendChild(toastContainer);
        }
        return toastContainer;
    }

    /**
     * @param {string} message
     * @param {'success' | 'error' | 'info'} [type='success']
     */
    function showToast(message, type = 'success') {
        const container = getToastContainer();
        const toast = document.createElement('div');
        
        const bgColor = type === 'success' 
            ? 'bg-emerald-600' 
            : type === 'error' 
            ? 'bg-red-600' 
            : 'bg-slate-700';
        
        toast.className = `${bgColor} text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-2 transform transition-all duration-300 translate-y-2 opacity-0`;
        
        // Add icon based on type
        if (type === 'success') {
            toast.innerHTML = `<svg class="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>`;
        } else if (type === 'error') {
            toast.innerHTML = `<svg class="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>`;
        }
        
        // Use textContent to prevent XSS
        const messageSpan = document.createElement('span');
        messageSpan.className = 'text-sm font-medium';
        messageSpan.textContent = message;
        toast.appendChild(messageSpan);
        
        container.appendChild(toast);
        
        // Trigger animation
        requestAnimationFrame(() => {
            toast.classList.remove('translate-y-2', 'opacity-0');
        });
        
        // Auto-remove after 2.5s
        setTimeout(() => {
            toast.classList.add('translate-y-2', 'opacity-0');
            setTimeout(() => toast.remove(), 300);
        }, 2500);
    }

    /**
     * @param {string} text
     * @param {string} [successMessage='Copied to clipboard']
     * @returns {Promise<boolean>}
     */
    async function copyToClipboard(text, successMessage = 'Copied to clipboard') {
        if (!text) {
            showToast('Nothing to copy', 'error');
            return false;
        }
        try {
            await navigator.clipboard.writeText(text);
            showToast(successMessage, 'success');
            return true;
        } catch {
            // Fallback for older browsers or when clipboard API fails
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            textarea.style.left = '-9999px';
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            try {
                const success = document.execCommand('copy');
                if (success) {
                    showToast(successMessage, 'success');
                    return true;
                } else {
                    showToast('Failed to copy', 'error');
                    return false;
                }
            } catch {
                showToast('Failed to copy', 'error')
                return false;
            } finally {
                document.body.removeChild(textarea);
            }
        }
    }

    /**
     * @param {string} content
     * @param {string} filename
     * @param {string} [mimeType='application/json']
     */
    function downloadFile(content, filename, mimeType = 'application/json') {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`Downloaded ${filename}`, 'success');
    }

    /**
     * @param {unknown[]} data
     * @param {string} filename
     */
    function exportAsCSV(data, filename) {
        // data should be an array of objects or array of arrays
        if (!data || data.length === 0) {
            showToast('No data to export', 'error');
            return;
        }

        let csv;
        if (Array.isArray(data[0])) {
            // Array of arrays
            csv = /** @type {unknown[][]} */ (data).map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');
        } else if (typeof data[0] === 'string') {
            // Simple array of strings
            csv = /** @type {string[]} */ (data).map(item => `"${item.replace(/"/g, '""')}"`).join('\n');
        } else {
            // Array of objects
            const headers = Object.keys(/** @type {object} */ (data[0]));
            const rows = /** @type {Record<string, unknown>[]} */ (data).map(obj => headers.map(h => `"${String(obj[h] || '').replace(/"/g, '""')}"`).join(','));
            csv = [headers.join(','), ...rows].join('\n');
        }

        downloadFile(csv, filename, 'text/csv');
    }

    // Expose globally (using custom property to avoid Clipboard API conflict)
    /** @type {ClipboardUtils} */
    const clipboardUtils = {
        copy: copyToClipboard,
        download: downloadFile,
        exportCSV: exportAsCSV,
        toast: showToast
    };
    // @ts-ignore - intentionally extending window with custom Clipboard property
    window.Clipboard = clipboardUtils;
})();
