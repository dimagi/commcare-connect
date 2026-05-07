module.exports = {
  env: {
    browser: true,
    es2020: true,
  },
  parserOptions: {
    ecmaVersion: 2020,
    sourceType: 'module',
  },
  extends: ['eslint:recommended'],
  rules: {
    'no-console': 'error',
    'no-unused-vars': 'error',
  },
  overrides: [
    {
      // Grandfather existing files — all rules are warnings only
      files: [
        'commcare_connect/static/js/alpine.js',
        'commcare_connect/static/js/dashboard.js',
        'commcare_connect/static/js/flatpickr.js',
        'commcare_connect/static/js/gtx.js',
        'commcare_connect/static/js/htmx.js',
        'commcare_connect/static/js/livesession.js',
        'commcare_connect/static/js/mapbox.js',
        'commcare_connect/static/js/tomselect.js',
        'commcare_connect/static/js/vendors.js',
      ],
      rules: {
        'no-console': 'warn',
        'no-unused-vars': 'warn',
        'no-undef': 'warn',
      },
    },
  ],
  ignorePatterns: [
    '.eslintrc.js',
    'commcare_connect/static/bundles/',
    'node_modules/',
  ],
};
