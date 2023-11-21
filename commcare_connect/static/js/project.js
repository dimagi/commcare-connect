import '../sass/project.scss';

/* Project specific Javascript goes here. */

document.body.addEventListener('appsRefreshed', function (evt) {
  document.getElementById('refresh-learn-spinner').remove();
  document.getElementById('refresh-deliver-spinner').remove();
});
