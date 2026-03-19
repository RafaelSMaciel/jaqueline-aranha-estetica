// Main JavaScript for Shiva Zen — UX + Template vendors
// Merged: original project JS + BootstrapMade Clinic template initializations

document.addEventListener('DOMContentLoaded', function() {
    // === Original Project Functions ===
    initScrollToTop();
    initHeaderScroll();
    initSmoothScroll();
    initFormValidation();
    initButtonLoading();
    initScrollAnimations();
    initMobileMenu();

    // === Template Vendor Initializations ===
    initAOS();
    initGLightbox();
    initPureCounter();
    initSwiper();
    initFAQ();
});

// ══════════════════════════════════════════════
// ORIGINAL PROJECT FUNCTIONS
// ══════════════════════════════════════════════

// Scroll to top button
function initScrollToTop() {
    // Check if scroll-top element already exists in HTML
    let scrollBtn = document.querySelector('.scroll-top');
    
    if (!scrollBtn) {
        scrollBtn = document.createElement('button');
        scrollBtn.className = 'scroll-to-top scroll-top';
        scrollBtn.innerHTML = '<i class="fas fa-arrow-up"></i>';
        scrollBtn.setAttribute('aria-label', 'Voltar ao topo');
        document.body.appendChild(scrollBtn);
    }
    
    function toggleScrollTop() {
        if (window.scrollY > 300) {
            scrollBtn.classList.add('visible');
            scrollBtn.classList.add('active');
        } else {
            scrollBtn.classList.remove('visible');
            scrollBtn.classList.remove('active');
        }
    }

    window.addEventListener('scroll', toggleScrollTop);
    window.addEventListener('load', toggleScrollTop);
    
    scrollBtn.addEventListener('click', function(e) {
        e.preventDefault();
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
}

// Header scroll effect
function initHeaderScroll() {
    const header = document.querySelector('.header-container');
    if (!header) return;
    
    let lastScroll = 0;
    window.addEventListener('scroll', function() {
        const currentScroll = window.pageYOffset;
        
        if (currentScroll > 100) {
            header.classList.add('scrolled');
            document.body.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
            document.body.classList.remove('scrolled');
        }
        
        lastScroll = currentScroll;
    });
}

// Smooth scroll for anchor links
function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href === '#' || href === '#!') return;
            
            const target = document.querySelector(href);
            if (target) {
                e.preventDefault();
                const headerOffset = 80;
                const elementPosition = target.getBoundingClientRect().top;
                const offsetPosition = elementPosition + window.pageYOffset - headerOffset;
                
                window.scrollTo({
                    top: offsetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });
}

// Form validation enhancements
function initFormValidation() {
    const forms = document.querySelectorAll('form');
    
    forms.forEach(form => {
        const inputs = form.querySelectorAll('input, select, textarea');
        
        inputs.forEach(input => {
            input.addEventListener('blur', function() {
                validateField(this);
            });
            
            input.addEventListener('input', function() {
                if (this.classList.contains('is-invalid')) {
                    validateField(this);
                }
            });
        });
        
        form.addEventListener('submit', function(e) {
            let isValid = true;
            
            inputs.forEach(input => {
                if (!validateField(input)) {
                    isValid = false;
                }
            });
            
            if (!isValid) {
                e.preventDefault();
                const firstInvalid = form.querySelector('.is-invalid');
                if (firstInvalid) {
                    firstInvalid.focus();
                    firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        });
    });
}

function validateField(field) {
    const value = field.value.trim();
    let isValid = true;
    let errorMessage = '';
    
    field.classList.remove('is-valid', 'is-invalid');
    const feedback = field.parentElement.querySelector('.invalid-feedback');
    if (feedback) feedback.remove();
    
    if (field.hasAttribute('required') && !value) {
        isValid = false;
        errorMessage = 'Este campo é obrigatório';
    }
    
    if (field.type === 'email' && value) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(value)) {
            isValid = false;
            errorMessage = 'Por favor, insira um e-mail válido';
        }
    }
    
    if (field.type === 'password' && value) {
        if (value.length < 6) {
            isValid = false;
            errorMessage = 'A senha deve ter pelo menos 6 caracteres';
        }
    }
    
    if (isValid && value) {
        field.classList.add('is-valid');
    } else if (!isValid) {
        field.classList.add('is-invalid');
        const feedbackDiv = document.createElement('div');
        feedbackDiv.className = 'invalid-feedback';
        feedbackDiv.textContent = errorMessage;
        field.parentElement.appendChild(feedbackDiv);
    }
    
    return isValid;
}

// Button loading states
function initButtonLoading() {
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function(e) {
            const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (submitBtn && !submitBtn.disabled) {
                const originalText = submitBtn.innerHTML;
                submitBtn.classList.add('loading');
                submitBtn.disabled = true;
                
                if (!submitBtn.querySelector('.loading-spinner')) {
                    const spinner = document.createElement('span');
                    spinner.className = 'loading-spinner';
                    submitBtn.innerHTML = '';
                    submitBtn.appendChild(spinner);
                }
                
                setTimeout(() => {
                    submitBtn.classList.remove('loading');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = originalText;
                }, 10000);
            }
        });
    });
}

// Scroll animations with Intersection Observer
function initScrollAnimations() {
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };
    
    const observer = new IntersectionObserver(function(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);
    
    document.querySelectorAll('.card, .service-card, .testimonial-card, .gallery-card').forEach(el => {
        observer.observe(el);
    });
}

// Mobile menu improvements
function initMobileMenu() {
    const toggler = document.querySelector('.navbar-toggler');
    const menu = document.querySelector('#mobileMenu');
    
    if (!toggler || !menu) return;
    
    document.addEventListener('click', function(e) {
        if (menu.classList.contains('show') && 
            !menu.contains(e.target) && 
            !toggler.contains(e.target)) {
            const bsCollapse = bootstrap.Collapse.getInstance(menu);
            if (bsCollapse) {
                bsCollapse.hide();
            }
        }
    });
    
    menu.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', function() {
            const bsCollapse = bootstrap.Collapse.getInstance(menu);
            if (bsCollapse) {
                bsCollapse.hide();
            }
        });
    });
    
    menu.addEventListener('shown.bs.collapse', function() {
        const items = menu.querySelectorAll('.nav-item');
        items.forEach((item, index) => {
            item.style.opacity = '0';
            item.style.transform = 'translateX(-20px)';
            setTimeout(() => {
                item.style.transition = 'all 0.3s ease';
                item.style.opacity = '1';
                item.style.transform = 'translateX(0)';
            }, index * 50);
        });
    });
}

// ══════════════════════════════════════════════
// TEMPLATE VENDOR INITIALIZATIONS
// ══════════════════════════════════════════════

// AOS — Animate On Scroll
function initAOS() {
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 600,
            easing: 'ease-in-out',
            once: true,
            mirror: false
        });
    }
}

// GLightbox — Lightbox for images/videos
function initGLightbox() {
    if (typeof GLightbox !== 'undefined') {
        GLightbox({
            selector: '.glightbox'
        });
    }
}

// PureCounter — Animated counters
function initPureCounter() {
    if (typeof PureCounter !== 'undefined') {
        new PureCounter();
    }
}

// Swiper — Carousels/Sliders
function initSwiper() {
    if (typeof Swiper === 'undefined') return;
    
    document.querySelectorAll(".init-swiper").forEach(function(swiperElement) {
        if (!swiperElement) return;
        
        const configEl = swiperElement.querySelector(".swiper-config");
        if (!configEl) return;
        
        let config = JSON.parse(configEl.innerHTML.trim());

        if (swiperElement.classList.contains("swiper-tab")) {
            initSwiperWithCustomPagination(swiperElement, config);
        } else {
            new Swiper(swiperElement, config);
        }
    });
}

// FAQ Toggle (template style accordions)
function initFAQ() {
    document.querySelectorAll('.faq-item h3, .faq-item .faq-toggle, .faq-item .faq-header').forEach((faqItem) => {
        faqItem.addEventListener('click', () => {
            faqItem.parentNode.classList.toggle('faq-active');
        });
    });
}

// ══════════════════════════════════════════════
// UTILITY FUNCTIONS
// ══════════════════════════════════════════════

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}
