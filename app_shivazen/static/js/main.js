// Main JavaScript for UX improvements

document.addEventListener('DOMContentLoaded', function() {
    // Scroll to top button
    initScrollToTop();
    
    // Header scroll effect
    initHeaderScroll();
    
    // Smooth scroll for anchor links
    initSmoothScroll();
    
    // Form validation enhancements
    initFormValidation();
    
    // Loading states for buttons
    initButtonLoading();
    
    // Intersection Observer for fade-in animations
    initScrollAnimations();
    
    // Mobile menu improvements
    initMobileMenu();
});

// Scroll to top button
function initScrollToTop() {
    const scrollBtn = document.createElement('button');
    scrollBtn.className = 'scroll-to-top';
    scrollBtn.innerHTML = '<i class="fas fa-arrow-up"></i>';
    scrollBtn.setAttribute('aria-label', 'Voltar ao topo');
    document.body.appendChild(scrollBtn);
    
    window.addEventListener('scroll', function() {
        if (window.pageYOffset > 300) {
            scrollBtn.classList.add('visible');
        } else {
            scrollBtn.classList.remove('visible');
        }
    });
    
    scrollBtn.addEventListener('click', function() {
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
        } else {
            header.classList.remove('scrolled');
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
            // Real-time validation feedback
            input.addEventListener('blur', function() {
                validateField(this);
            });
            
            input.addEventListener('input', function() {
                if (this.classList.contains('is-invalid')) {
                    validateField(this);
                }
            });
        });
        
        // Form submission validation
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
    
    // Remove previous validation classes
    field.classList.remove('is-valid', 'is-invalid');
    const feedback = field.parentElement.querySelector('.invalid-feedback');
    if (feedback) feedback.remove();
    
    // Required validation
    if (field.hasAttribute('required') && !value) {
        isValid = false;
        errorMessage = 'Este campo é obrigatório';
    }
    
    // Email validation
    if (field.type === 'email' && value) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(value)) {
            isValid = false;
            errorMessage = 'Por favor, insira um e-mail válido';
        }
    }
    
    // Password validation
    if (field.type === 'password' && value) {
        if (value.length < 6) {
            isValid = false;
            errorMessage = 'A senha deve ter pelo menos 6 caracteres';
        }
    }
    
    // Apply validation classes
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
                
                // Re-enable after 10 seconds as fallback
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
    
    // Observe elements with animation classes
    document.querySelectorAll('.card, .service-card, .testimonial-card, .gallery-card').forEach(el => {
        observer.observe(el);
    });
}

// Mobile menu improvements
function initMobileMenu() {
    const toggler = document.querySelector('.navbar-toggler');
    const menu = document.querySelector('#mobileMenu');
    
    if (!toggler || !menu) return;
    
    // Close menu when clicking outside
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
    
    // Close menu when clicking a link
    menu.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', function() {
            const bsCollapse = bootstrap.Collapse.getInstance(menu);
            if (bsCollapse) {
                bsCollapse.hide();
            }
        });
    });
    
    // Animate menu items
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

// Utility: Debounce function
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

// Utility: Throttle function
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

