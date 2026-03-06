import Alpine from 'alpinejs';
import Persist from '@alpinejs/persist';
import Tooltip from '@ryangjchandler/alpine-tooltip';
Alpine.plugin(Persist);
Alpine.plugin(Tooltip);
window.Alpine = Alpine;
window.Alpine.start();
import 'tippy.js/dist/tippy.css';
