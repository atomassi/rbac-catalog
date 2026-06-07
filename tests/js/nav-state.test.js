/**
 * Unit tests for nav-state.js
 * Tests navigation state management and back button logic
 */
import { describe, it, expect, beforeEach } from 'vitest';
const { NavState } = require('../../rbaccatalog/web/static/js/nav-state.js');

describe('NavState', () => {
    beforeEach(() => {
        // Clear sessionStorage before each test
        sessionStorage.clear();
        // Reset debug mode
        // @ts-ignore
        window.NavStateDebug = false;
    });

    describe('get/set', () => {
        it('should return empty object when no state stored', () => {
            expect(NavState.get()).toEqual({});
        });

        it('should store and retrieve state', () => {
            NavState.set({ back: 'roles' });
            expect(NavState.get()).toEqual({ back: 'roles' });
        });

        it('should merge with existing state', () => {
            NavState.set({ back: 'roles' });
            NavState.set({ from_role_id: '123' });
            expect(NavState.get()).toEqual({ 
                back: 'roles', 
                from_role_id: '123' 
            });
        });

        it('should remove null values', () => {
            NavState.set({ back: 'roles', from_role_id: '123' });
            NavState.set({ from_role_id: null });
            expect(NavState.get()).toEqual({ back: 'roles' });
        });

        it('should remove undefined values', () => {
            NavState.set({ back: 'roles', from_role_id: '123' });
            NavState.set({ from_role_id: undefined });
            expect(NavState.get()).toEqual({ back: 'roles' });
        });

        it('should remove empty string values', () => {
            NavState.set({ back: 'roles', from_role_id: '123' });
            NavState.set({ from_role_id: '' });
            expect(NavState.get()).toEqual({ back: 'roles' });
        });
    });

    describe('clear', () => {
        it('should remove all state', () => {
            NavState.set({ back: 'roles', from_role_id: '123' });
            NavState.clear();
            expect(NavState.get()).toEqual({});
        });
    });

    describe('isAiMode/setAiMode', () => {
        it('should return false by default', () => {
            expect(NavState.isAiMode()).toBe(false);
        });

        it('should return true when AI mode is enabled', () => {
            NavState.setAiMode(true);
            expect(NavState.isAiMode()).toBe(true);
        });

        it('should return false when AI mode is disabled', () => {
            NavState.setAiMode(true);
            NavState.setAiMode(false);
            expect(NavState.isAiMode()).toBe(false);
        });
    });

    describe('setBackContext', () => {
        it('should store back target', () => {
            NavState.setBackContext('roles');
            const state = NavState.get();
            expect(state.back).toBe('roles');
        });

        it('should store back target with context', () => {
            NavState.setBackContext('role', { 
                from_role_id: 'abc-123', 
                from_role_slug: 'owner-role', 
                from_role_name: 'Owner' 
            });
            const state = NavState.get();
            expect(state.back).toBe('role');
            expect(state.from_role_id).toBe('abc-123');
            expect(state.from_role_slug).toBe('owner-role');
            expect(state.from_role_name).toBe('Owner');
        });

        it('should store operation context', () => {
            NavState.setBackContext('operation', { 
                from_operation: 'Microsoft.Storage/read' 
            });
            const state = NavState.get();
            expect(state.back).toBe('operation');
            expect(state.from_operation).toBe('Microsoft.Storage/read');
        });
    });

    describe('getBackUrl', () => {
        it('should return /recommend for recommend back target', () => {
            NavState.set({ back: 'recommend' });
            expect(NavState.getBackUrl()).toBe('/recommend');
        });

        it('should return /recommend even when AI mode enabled (no ai param)', () => {
            NavState.set({ back: 'recommend' });
            NavState.setAiMode(true);
            expect(NavState.getBackUrl()).toBe('/recommend');
        });

        it('should return /roles for roles back target', () => {
            NavState.set({ back: 'roles' });
            expect(NavState.getBackUrl()).toBe('/roles');
        });

        it('should return /operations for operations back target', () => {
            NavState.set({ back: 'operations' });
            expect(NavState.getBackUrl()).toBe('/operations');
        });

        it('should return specific operation path for operation back target', () => {
            NavState.set({ back: 'operation', from_operation: 'Microsoft.Storage/read' });
            expect(NavState.getBackUrl()).toBe('/operations/Microsoft.Storage%2Fread');
        });

        it('should return specific role path for role back target', () => {
            NavState.set({ 
                back: 'role', 
                from_role_id: 'abc-123',
                from_role_slug: 'my-role'
            });
            expect(NavState.getBackUrl()).toBe('/roles/abc-123/my-role');
        });

        it('should return /recent for recent back target', () => {
            NavState.set({ back: 'recent' });
            expect(NavState.getBackUrl()).toBe('/recent');
        });

        it('should return /analytics for analytics back target', () => {
            NavState.set({ back: 'analytics' });
            expect(NavState.getBackUrl()).toBe('/analytics');
        });

        it('should return default /recent for unknown back target', () => {
            NavState.set({ back: 'unknown' });
            expect(NavState.getBackUrl()).toBe('/recent');
        });

        it('should use default back target when provided', () => {
            // No state set
            expect(NavState.getBackUrl({ back: 'roles' })).toBe('/roles');
        });
    });

    describe('getBackLabel', () => {
        it('should return "Back to Recommender" for recommend', () => {
            NavState.set({ back: 'recommend' });
            expect(NavState.getBackLabel()).toBe('Back to Recommender');
        });

        it('should return "Back to Roles" for roles', () => {
            NavState.set({ back: 'roles' });
            expect(NavState.getBackLabel()).toBe('Back to Roles');
        });

        it('should return "Back to Operations" for operations', () => {
            NavState.set({ back: 'operations' });
            expect(NavState.getBackLabel()).toBe('Back to Operations');
        });

        it('should return "Back to Operation" for operation back target', () => {
            NavState.set({ back: 'operation', from_operation: 'Microsoft.Storage/read' });
            expect(NavState.getBackLabel()).toBe('Back to Operation');
        });

        it('should return role name for role back target', () => {
            NavState.set({ back: 'role', from_role_name: 'Owner' });
            expect(NavState.getBackLabel()).toBe('Back to Owner');
        });

        it('should return generic "Back to Role" when role name missing', () => {
            NavState.set({ back: 'role' });
            expect(NavState.getBackLabel()).toBe('Back to Role');
        });

        it('should return "Back to Analytics" for analytics', () => {
            NavState.set({ back: 'analytics' });
            expect(NavState.getBackLabel()).toBe('Back to Analytics');
        });

        it('should return "Back to Recent" for recent/unknown', () => {
            NavState.set({ back: 'recent' });
            expect(NavState.getBackLabel()).toBe('Back to Recent');
        });
    });

    describe('consumeBackContext', () => {
        it('should clear state after consuming', () => {
            NavState.set({ back: 'roles' });
            NavState.consumeBackContext();
            expect(NavState.get()).toEqual({});
        });
    });

    describe('error handling', () => {
        it('should handle sessionStorage errors gracefully', () => {
            // Mock sessionStorage to throw
            const originalGetItem = sessionStorage.getItem;
            sessionStorage.getItem = () => { throw new Error('Storage error'); };
            
            // Should not throw and return default value
            expect(NavState.get()).toEqual({});
            
            // Restore
            sessionStorage.getItem = originalGetItem;
        });
    });
});
