const path = require('path');
const BundleTracker = require('webpack-bundle-tracker');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const Dotenv = require('dotenv-webpack');

module.exports = {
  target: 'web',
  context: path.join(__dirname, '../'),
  entry: {
    project: path.resolve(__dirname, '../commcare_connect/static/js/project'),
    dashboard: path.resolve(
      __dirname,
      '../commcare_connect/static/js/dashboard',
    ),
    vendors: path.resolve(__dirname, '../commcare_connect/static/js/vendors'),
  },
  output: {
    path: path.resolve(__dirname, '../commcare_connect/static/bundles/'),
    publicPath: '/static/bundles/',
    filename: 'js/[name]-bundle.js',
    chunkFilename: 'js/[name]-bundle.js',
  },
  plugins: [
    new BundleTracker({
      path: path.resolve(path.join(__dirname, '../')),
      filename: 'webpack-stats.json',
    }),
    new MiniCssExtractPlugin({ filename: 'css/[name].css' }),
    new Dotenv({ path: './.env' }),
  ],
  module: {
    rules: [
      // we pass the output from babel loader to react-hot loader
      {
        test: /\.js$/,
        loader: 'babel-loader',
      },
      {
        test: /\.s?css$/i,
        use: [
          MiniCssExtractPlugin.loader,
          'css-loader',
          {
            loader: 'postcss-loader',
            options: {
              postcssOptions: {
                plugins: ['postcss-preset-env', 'autoprefixer', 'pixrem'],
              },
            },
          },
          'sass-loader',
        ],
      },
    ],
  },
  resolve: {
    modules: ['node_modules'],
    extensions: ['.js', '.jsx'],
  },
  devtool: 'eval-cheap-source-map',
};
