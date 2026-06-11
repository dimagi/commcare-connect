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
    'no-empty': ['error', { allowEmptyCatch: true }],
  },
  ignorePatterns: [
    'node_modules/',
    'commcare_connect/static/bundles/',
    // Vendor tracking snippet (LiveSession) — minified third-party code, not ours to lint
    'commcare_connect/static/js/livesession.js',
  ],
};
