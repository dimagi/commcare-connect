import Alpine from 'alpinejs';
import Tooltip from '@ryangjchandler/alpine-tooltip';
Alpine.plugin(Tooltip);
window.Alpine = Alpine;
import './project_alpine.js';
window.Alpine.start();
import 'tippy.js/dist/tippy.css';
