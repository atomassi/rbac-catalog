// @ts-check
/**
 * Clipboard utilities with toast notifications
 * @typedef {{
 *   copy: (text: string, successMessage?: string) => Promise<boolean>,
 *   copyWithFeedback: (button: HTMLElement, text: string, successMessage?: string, feedbackDuration?: number) => Promise<boolean>,
 *   copyWithTooltip: (button: HTMLElement, text: string, feedbackDuration?: number) => Promise<boolean>,
 *   download: (content: string, filename: string, mimeType?: string) => void
 * }} ClipboardUtils
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
        
        const success = await _copyToClipboardSilent(text);
        if (success) {
            showToast(successMessage, 'success');
        } else {
            showToast('Failed to copy', 'error');
        }
        return success;
    }

    /**
     * Core clipboard copy logic without UI feedback (private helper)
     * @param {string} text - The text to copy
     * @returns {Promise<boolean>}
     */
    async function _copyToClipboardSilent(text) {
        if (typeof text !== 'string' || text.trim().length === 0) {
            return false;
        }
        
        // Try modern clipboard API first (if available and permitted)
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch {
                // Fall through to legacy fallback
            }
        }
        
        // Fallback for older browsers or when clipboard API fails/unavailable
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        try {
            return document.execCommand('copy');
        } catch {
            return false;
        } finally {
            document.body.removeChild(textarea);
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
     * Copy text and show visual feedback on the button (icon/text toggle)
     * Expects button to contain .copy-icon, .check-icon, .copy-text, .copied-text elements within it
     * @param {HTMLElement} button - The button element to update
     * @param {string} text - The text to copy
     * @param {string} [successMessage='Copied to clipboard'] - Toast message on success
     * @param {number} [feedbackDuration=1500] - How long to show the feedback state (ms)
     * @returns {Promise<boolean>}
     */
    async function copyWithFeedback(button, text, successMessage = 'Copied to clipboard', feedbackDuration = 1500) {
        const success = await copyToClipboard(text, successMessage);
        
        if (success && button) {
            // Toggle to "copied" state using inline styles (overrides CSS classes)
            const copyIcon = /** @type {HTMLElement | null} */ (button.querySelector('.copy-icon'));
            const checkIcon = /** @type {HTMLElement | null} */ (button.querySelector('.check-icon'));
            const copyText = /** @type {HTMLElement | null} */ (button.querySelector('.copy-text'));
            const copiedText = /** @type {HTMLElement | null} */ (button.querySelector('.copied-text'));
            
            if (copyIcon) copyIcon.style.display = 'none';
            if (checkIcon) checkIcon.style.display = '';
            if (copyText) copyText.style.display = 'none';
            if (copiedText) copiedText.style.display = '';
            
            // Revert after duration
            setTimeout(() => {
                if (copyIcon) copyIcon.style.display = '';
                if (checkIcon) checkIcon.style.display = 'none';
                if (copyText) copyText.style.display = '';
                if (copiedText) copiedText.style.display = 'none';
            }, feedbackDuration);
        }
        
        return success;
    }

    /**
     * Copy with tooltip feedback (no toast)
     * Expects button to contain .copy-icon, .check-icon, .copied-tooltip elements
     * @param {HTMLElement} button - The button element to update
     * @param {string} text - The text to copy
     * @param {number} [feedbackDuration=1500] - How long to show the feedback state (ms)
     * @returns {Promise<boolean>}
     */
    async function copyWithTooltip(button, text, feedbackDuration = 1500) {
        const success = await _copyToClipboardSilent(text);
        
        if (success && button) {
            // Toggle icons and show tooltip
            const copyIcon = /** @type {HTMLElement | null} */ (button.querySelector('.copy-icon'));
            const checkIcon = /** @type {HTMLElement | null} */ (button.querySelector('.check-icon'));
            const tooltip = /** @type {HTMLElement | null} */ (button.querySelector('.copied-tooltip'));
            
            if (copyIcon) copyIcon.style.display = 'none';
            if (checkIcon) checkIcon.style.display = '';
            if (tooltip) {
                tooltip.style.visibility = 'visible';
                tooltip.style.opacity = '1';
            }
            
            // Revert after duration
            setTimeout(() => {
                if (copyIcon) copyIcon.style.display = '';
                if (checkIcon) checkIcon.style.display = 'none';
                if (tooltip) {
                    tooltip.style.opacity = '0';
                    tooltip.style.visibility = 'hidden';
                }
            }, feedbackDuration);
        }
        
        return success;
    }

    // Expose globally (using custom property to avoid Clipboard API conflict)
    /** @type {ClipboardUtils} */
    const clipboardUtils = {
        copy: copyToClipboard,
        copyWithFeedback: copyWithFeedback,
        copyWithTooltip: copyWithTooltip,
        download: downloadFile
    };

    // Browser: expose on window
    if (typeof window !== 'undefined') {
        // @ts-ignore - intentionally extending window with custom Clipboard property
        window.Clipboard = clipboardUtils;
    }

    // Node.js/Vitest: export internals for testing
    if (typeof module !== 'undefined' && module.exports) {
        // @ts-ignore - CommonJS export for Node.js test environment (module is Node-specific)
        module.exports = {
            clipboardUtils,
            // Export internals for unit testing
            _internals: {
                getToastContainer,
                showToast,
                _copyToClipboardSilent
            }
        };
    }
})();
