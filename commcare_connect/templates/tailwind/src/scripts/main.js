 function toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        const commcareText = document.getElementById('commcareText');
        const menuIcon = document.getElementById('menuIcon');
        const menuText = document.getElementById('menuText');
        const navItems = document.getElementById('navItems');
        const sidebarTexts = document.querySelectorAll('.sidebar-text');
        const bottomItems = document.querySelectorAll('.pb-4 > div');
        
         
        sidebar.classList.toggle('w-16');
        sidebar.classList.toggle('w-64');

        commcareText.classList.toggle('hidden');
        menuIcon.classList.toggle('fa-bars');
        menuIcon.classList.toggle('fa-xmark');
        menuText.classList.toggle('hidden');

        navItems.querySelectorAll('div').forEach(navItem => {
            navItem.classList.toggle('w-10');
            navItem.classList.toggle('w-full');
        });

        bottomItems.forEach(item => {
            item.classList.toggle('w-10');
            item.classList.toggle('w-full');
        });

        sidebarTexts.forEach(text => {
            text.classList.toggle('hidden');
        });
    }

    
function dashboardCartOpen() {
    const dropdown = document.getElementById('dropdown-content');
    dropdown.classList.toggle('hidden');
}

// function imageCarousel() {
//     const imagesContainer = document.querySelector('.values');

//     if (!imagesContainer) {
//         console.error("Error: .values container not found.");
//         return;  
//     }

//     const images = Array.from(imagesContainer.querySelectorAll('img'));
//     const leftArrow = document.querySelector('.fa-arrow-left');
//     const rightArrow = document.querySelector('.fa-arrow-right');

//     if (!leftArrow || !rightArrow) {
//         console.error("Error: left or right arrow not found.");
//         return;
//     }

//     let currentIndex = 0;
//     const totalImages = images.length;
//     const visibleImages = 3;

//     function updateCarousel() {
//         images.forEach((img, index) => {
//             if (index >= currentIndex && index < currentIndex + visibleImages) {
//                 img.style.display = 'block';
//             } else {
//                 img.style.display = 'none';
//             }
//         });
//     }

//     function moveLeft() {
//         currentIndex = Math.max(0, currentIndex - 1);
//         updateCarousel();
//     }

//     function moveRight() {
//         currentIndex = Math.min(totalImages - visibleImages, currentIndex + 1);
//         updateCarousel();
//     }

//     leftArrow.addEventListener('click', moveLeft);
//     rightArrow.addEventListener('click', moveRight);

//     updateCarousel();  
// }

// document.addEventListener('DOMContentLoaded', function() {
//     imageCarousel();
// });





document.addEventListener('htmx:load', () => {
    // Attach tab functionality for dynamically loaded components
    document.querySelectorAll('.tab-item').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab-item').forEach(item => item.classList.remove('active'));
            tab.classList.add('active');

            document.querySelectorAll('.tab-content').forEach(content => content.classList.add('hidden'));
            document.getElementById(tab.getAttribute('data-tab')).classList.remove('hidden');
        });
    });
});

