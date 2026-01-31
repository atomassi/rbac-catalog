import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        include: ['tests/js/**/*.test.js'],
        globals: true,
        coverage: {
            provider: 'v8',
            include: ['azurerbac/web/static/js/**/*.js'],
            exclude: ['**/*.test.js'],
        },
    },
});
