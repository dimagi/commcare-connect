const js = require('@eslint/js');
const globals = require('globals');

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
    files: ['commcare_connect/static/**/*.js'],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      globals: globals.browser,
    },
    rules: {
      'no-console': 'error',
      // eslint 9 flipped `caughtErrors` to 'all'; `catch (_)` is our marker
      // for a deliberately ignored error, so keep it exempt.
      'no-unused-vars': ['error', { caughtErrorsIgnorePattern: '^_$' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    // The progress map uses the copies vendors-bundle.js and mapbox-bundle.js put on window;
    // importing them would bundle a second Alpine/Mapbox (see microplanning/index.js).
    files: ['commcare_connect/static/js/microplanning/**/*.js'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['alpinejs', 'mapbox-gl', 'htmx.org'],
              message: 'Use the window global instead (see index.js).',
            },
          ],
        },
      ],
    },
  },
];
