const js = require('@eslint/js');
const globals = require('globals');
const tseslint = require('typescript-eslint');

module.exports = [
  {
    ignores: [
      'node_modules/',
      'commcare_connect/static/bundles/',
      // Build output and third-party trees. Flat config no longer ignores
      // dot-directories by default, so .venv must be listed explicitly.
      '.venv/',
      'staticfiles/',
      // Vendor tracking snippet (LiveSession) — minified third-party code, not ours to lint
      'commcare_connect/static/js/livesession.js',
    ],
  },
  js.configs.recommended,
  {
    // Build tooling: CommonJS, runs on Node
    files: ['eslint.config.js', 'webpack/*.config.js'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: globals.node,
    },
  },
  {
    files: ['commcare_connect/static/**/*.{js,jsx,ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        ...globals.browser,
        // Django's JavaScript catalog (the `javascript-catalog` URL)
        gettext: 'readonly',
        ngettext: 'readonly',
        interpolate: 'readonly',
      },
    },
    rules: {
      'no-console': 'error',
      // eslint 9 flipped `caughtErrors` to 'all'; `catch (_)` is our marker
      // for a deliberately ignored error, so keep it exempt.
      'no-unused-vars': ['error', { caughtErrorsIgnorePattern: '^_$' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  ...tseslint.configs.recommended.map((config) => ({
    ...config,
    files: ['commcare_connect/static/**/*.{ts,tsx}'],
  })),
  {
    files: ['commcare_connect/static/**/*.{ts,tsx}'],
    rules: {
      // TypeScript reports undefined names itself, and knows about types that ESLint doesn't.
      'no-undef': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { caughtErrorsIgnorePattern: '^_$' },
      ],
    },
  },
];
