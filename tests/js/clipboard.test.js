/**
 * Unit tests for clipboard.js
 * Tests clipboard utilities and toast notifications
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// Must import after jsdom is set up
const { clipboardUtils, _internals } = require('../../azurerbac/web/static/js/clipboard.js');

describe('clipboard', () => {
    beforeEach(() => {
        // Clear DOM but keep toast container so the module's cached reference stays valid
        document.body.innerHTML = '';
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('getToastContainer', () => {
        it('should create toast container on first call', () => {
            const container = _internals.getToastContainer();
            expect(container).toBeDefined();
            expect(container.id).toBe('toast-container');
            expect(document.body.contains(container)).toBe(true);
        });

        it('should reuse existing container on subsequent calls', () => {
            const container1 = _internals.getToastContainer();
            const container2 = _internals.getToastContainer();
            expect(container1).toBe(container2);
        });

        it('should have correct CSS classes', () => {
            const container = _internals.getToastContainer();
            expect(container.className).toContain('fixed');
            expect(container.className).toContain('bottom-4');
            expect(container.className).toContain('right-4');
            expect(container.className).toContain('z-50');
        });
    });

    describe('showToast', () => {
        it('should create a toast element', () => {
            _internals.showToast('Test message', 'success');
            const container = _internals.getToastContainer();
            expect(container.children.length).toBeGreaterThanOrEqual(1);
        });

        it('should display the message text', () => {
            _internals.showToast('Hello World', 'success');
            const container = _internals.getToastContainer();
            expect(container.textContent).toContain('Hello World');
        });

        it('should use green background for success', () => {
            _internals.showToast('Success!', 'success');
            const container = _internals.getToastContainer();
            const toast = container.lastElementChild;
            expect(toast?.className).toContain('bg-emerald-600');
        });

        it('should use red background for error', () => {
            _internals.showToast('Error!', 'error');
            const container = _internals.getToastContainer();
            const toast = container.lastElementChild;
            expect(toast?.className).toContain('bg-red-600');
        });

        it('should use slate background for info', () => {
            _internals.showToast('Info!', 'info');
            const container = _internals.getToastContainer();
            const toast = container.lastElementChild;
            expect(toast?.className).toContain('bg-slate-700');
        });

        it('should include check icon for success', () => {
            _internals.showToast('Done', 'success');
            const container = _internals.getToastContainer();
            const svg = container.querySelector('svg');
            expect(svg).not.toBeNull();
        });

        it('should include X icon for error', () => {
            _internals.showToast('Failed', 'error');
            const container = _internals.getToastContainer();
            const svg = container.querySelector('svg');
            expect(svg).not.toBeNull();
        });

        it('should escape HTML in message (XSS prevention)', () => {
            _internals.showToast('<script>alert("xss")</script>', 'success');
            const container = _internals.getToastContainer();
            // textContent is used, so script tags are not executed
            expect(container.innerHTML).not.toContain('<script>');
            expect(container.textContent).toContain('<script>');
        });
    });

    describe('clipboardUtils.copy', () => {
        it('should show error toast for empty text', async () => {
            const result = await clipboardUtils.copy('');
            expect(result).toBe(false);
            const container = _internals.getToastContainer();
            expect(container.textContent).toContain('Nothing to copy');
        });

        it('should show error toast for null/undefined', async () => {
            // @ts-ignore - testing invalid input
            const result = await clipboardUtils.copy(null);
            expect(result).toBe(false);
        });

        it('should attempt to copy using clipboard API', async () => {
            const writeTextMock = vi.fn().mockResolvedValue(undefined);
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });

            const result = await clipboardUtils.copy('test text');
            expect(result).toBe(true);
            expect(writeTextMock).toHaveBeenCalledWith('test text');
        });

        it('should show success toast with custom message', async () => {
            const writeTextMock = vi.fn().mockResolvedValue(undefined);
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });

            await clipboardUtils.copy('test', 'Custom success!');
            const container = _internals.getToastContainer();
            expect(container.textContent).toContain('Custom success!');
        });

        it('should show error toast on clipboard failure', async () => {
            const writeTextMock = vi.fn().mockRejectedValue(new Error('Permission denied'));
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });
            // Also mock execCommand to fail
            document.execCommand = vi.fn().mockReturnValue(false);

            const result = await clipboardUtils.copy('test text');
            expect(result).toBe(false);
            const container = _internals.getToastContainer();
            expect(container.textContent).toContain('Failed to copy');
        });
    });

    describe('_copyToClipboardSilent', () => {
        it('should return false for empty string', async () => {
            const result = await _internals._copyToClipboardSilent('');
            expect(result).toBe(false);
        });

        it('should return false for whitespace-only string', async () => {
            const result = await _internals._copyToClipboardSilent('   ');
            expect(result).toBe(false);
        });

        it('should return false for non-string', async () => {
            // @ts-ignore - testing invalid input
            const result = await _internals._copyToClipboardSilent(123);
            expect(result).toBe(false);
        });

        it('should try clipboard API first', async () => {
            const writeTextMock = vi.fn().mockResolvedValue(undefined);
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });

            const result = await _internals._copyToClipboardSilent('hello');
            expect(result).toBe(true);
            expect(writeTextMock).toHaveBeenCalledWith('hello');
        });

        it('should fall back to execCommand if clipboard API fails', async () => {
            const writeTextMock = vi.fn().mockRejectedValue(new Error('Not allowed'));
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });
            document.execCommand = vi.fn().mockReturnValue(true);

            const result = await _internals._copyToClipboardSilent('fallback test');
            expect(result).toBe(true);
            expect(document.execCommand).toHaveBeenCalledWith('copy');
        });
    });

    describe('clipboardUtils.download', () => {
        it('should create and click a download link', () => {
            const createObjectURLMock = vi.fn().mockReturnValue('blob:test');
            const revokeObjectURLMock = vi.fn();
            URL.createObjectURL = createObjectURLMock;
            URL.revokeObjectURL = revokeObjectURLMock;

            clipboardUtils.download('{"test": true}', 'data.json');

            // Verify blob was created
            expect(createObjectURLMock).toHaveBeenCalled();
            
            // Verify URL was revoked
            expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:test');
            
            // Verify success toast
            const container = _internals.getToastContainer();
            expect(container.textContent).toContain('Downloaded data.json');
        });

        it('should use correct mime type', () => {
            const createObjectURLMock = vi.fn().mockReturnValue('blob:test');
            URL.createObjectURL = createObjectURLMock;
            URL.revokeObjectURL = vi.fn();

            clipboardUtils.download('plain text', 'file.txt', 'text/plain');

            // Check that Blob was created with correct type
            const blobArg = createObjectURLMock.mock.calls[0][0];
            expect(blobArg).toBeInstanceOf(Blob);
            expect(blobArg.type).toBe('text/plain');
        });
    });

    describe('clipboardUtils.copyWithFeedback', () => {
        beforeEach(() => {
            // Mock successful clipboard
            const writeTextMock = vi.fn().mockResolvedValue(undefined);
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });
        });

        it('should toggle icon visibility on success', async () => {
            // Create button with expected structure
            document.body.innerHTML = `
                <button id="test-btn">
                    <span class="copy-icon">Copy</span>
                    <span class="check-icon" style="display: none">Check</span>
                    <span class="copy-text">Copy</span>
                    <span class="copied-text" style="display: none">Copied</span>
                </button>
            `;
            const button = document.getElementById('test-btn');

            await clipboardUtils.copyWithFeedback(button, 'test text');

            const copyIcon = button?.querySelector('.copy-icon');
            const checkIcon = button?.querySelector('.check-icon');
            
            // After copy, icons should be toggled
            expect(copyIcon?.style.display).toBe('none');
            expect(checkIcon?.style.display).toBe('');
        });

        it('should return true on success', async () => {
            const button = document.createElement('button');
            const result = await clipboardUtils.copyWithFeedback(button, 'test');
            expect(result).toBe(true);
        });

        it('should handle null button gracefully', async () => {
            // Should not throw
            const result = await clipboardUtils.copyWithFeedback(null, 'test');
            expect(result).toBe(true); // Copy succeeded, just no visual feedback
        });
    });

    describe('clipboardUtils.copyWithTooltip', () => {
        beforeEach(() => {
            const writeTextMock = vi.fn().mockResolvedValue(undefined);
            Object.assign(navigator, {
                clipboard: { writeText: writeTextMock }
            });
        });

        it('should show tooltip on success', async () => {
            document.body.innerHTML = `
                <button id="test-btn">
                    <span class="copy-icon">Copy</span>
                    <span class="check-icon" style="display: none">✓</span>
                    <span class="copied-tooltip" style="visibility: hidden; opacity: 0">Copied!</span>
                </button>
            `;
            const button = document.getElementById('test-btn');

            await clipboardUtils.copyWithTooltip(button, 'test');

            const tooltip = button?.querySelector('.copied-tooltip');
            expect(tooltip?.style.visibility).toBe('visible');
            expect(tooltip?.style.opacity).toBe('1');
        });

        it('should return true on success', async () => {
            const button = document.createElement('button');
            const result = await clipboardUtils.copyWithTooltip(button, 'test');
            expect(result).toBe(true);
        });
    });
});
