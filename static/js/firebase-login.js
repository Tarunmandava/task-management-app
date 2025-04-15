// Firebase Authentication Handling
// Initialize Firebase auth and register callback handlers

document.addEventListener('DOMContentLoaded', function() {
    // Firebase authentication state observer
    firebase.auth().onAuthStateChanged(function(user) {
        if (user) {
            // User is signed in
            if (document.getElementById('login-section')) {
                document.getElementById('login-section').style.display = 'none';
            }
            if (document.getElementById('user-section')) {
                document.getElementById('user-section').style.display = 'block';
                document.getElementById('user-email').textContent = user.email;
            }
            
            // Get the id token for server-side verification
            user.getIdToken().then(function(idToken) {
                // Send the token to your server
                fetch('/verify-token', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include', // Important for cookies
                    body: JSON.stringify({
                        idToken: idToken
                    }),
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        console.log("Authentication successful");
                        
                        // If we're on the index page, redirect to dashboard
                        if (window.location.pathname === '/' || window.location.pathname === '/index.html') {
                            window.location.href = '/dashboard';
                        }
                    } else {
                        console.error("Authentication failed:", data.error);
                        firebase.auth().signOut();
                    }
                })
                .catch(error => {
                    console.error("Error:", error);
                });
            });
        } else {
            // User is signed out
            if (document.getElementById('login-section')) {
                document.getElementById('login-section').style.display = 'block';
            }
            if (document.getElementById('user-section')) {
                document.getElementById('user-section').style.display = 'none';
            }
            
            // If we're not on the index page, redirect to login
            if (window.location.pathname !== '/' && window.location.pathname !== '/index.html') {
                window.location.href = '/';
            }
        }
    });

    // Login form submission handler
    document.getElementById('login-form')?.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const errorElement = document.getElementById('login-error');
        
        // Clear previous errors
        if (errorElement) {
            errorElement.style.display = 'none';
        }
        
        // Show loading indicator if available
        const loginButton = document.querySelector('#login-form button[type="submit"]');
        if (loginButton) {
            loginButton.disabled = true;
            loginButton.innerHTML = 'Logging in...';
        }
        
        firebase.auth().signInWithEmailAndPassword(email, password)
        .then(function(userCredential) {
            // Successfully logged in - redirect will happen in onAuthStateChanged
            console.log("Login successful");
            // Clear any previous error messages
            if (errorElement) {
                errorElement.style.display = 'none';
            }
        })
        .catch(function(error) {
            // Handle errors
            if (errorElement) {
                errorElement.textContent = error.message;
                errorElement.style.display = 'block';
            }
            console.error("Login error:", error);
            
            // Reset login button
            if (loginButton) {
                loginButton.disabled = false;
                loginButton.innerHTML = 'Login';
            }
        });
    });
    
    // Register form submission handler
    document.getElementById('register-form')?.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const email = document.getElementById('register-email').value;
        const password = document.getElementById('register-password').value;
        const name = document.getElementById('register-name').value;
        const errorElement = document.getElementById('register-error');
        
        // Clear previous errors
        if (errorElement) {
            errorElement.style.display = 'none';
        }
        
        // Show loading indicator if available
        const registerButton = document.querySelector('#register-form button[type="submit"]');
        if (registerButton) {
            registerButton.disabled = true;
            registerButton.innerHTML = 'Creating account...';
        }
        
        firebase.auth().createUserWithEmailAndPassword(email, password)
        .then(function(userCredential) {
            // Clear any previous error messages
            if (errorElement) {
                errorElement.style.display = 'none';
            }
            
            // Update user profile with display name
            return userCredential.user.updateProfile({
                displayName: name
            }).then(() => {
                return userCredential;
            });
        })
        .then(function(userCredential) {
            // Create user profile
            return fetch('/create-user', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                credentials: 'include', // Important for cookies
                body: JSON.stringify({
                    uid: userCredential.user.uid,
                    email: email,
                    name: name
                }),
            });
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log("User profile created");
                // Redirect will happen automatically in onAuthStateChanged
            } else {
                throw new Error(data.error || "Failed to create user profile");
            }
        })
        .catch(function(error) {
            // Handle errors
            if (errorElement) {
                errorElement.textContent = error.message;
                errorElement.style.display = 'block';
            }
            console.error("Registration error:", error);
            
            // Reset register button
            if (registerButton) {
                registerButton.disabled = false;
                registerButton.innerHTML = 'Register';
            }
        });
    });
    
    // Logout button handler
    document.getElementById('logout-btn')?.addEventListener('click', function() {
        firebase.auth().signOut().then(function() {
            // Sign-out successful
            // Clear cookies by setting expired date
            document.cookie = "user_id=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            document.cookie = "email=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            document.cookie = "name=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            
            window.location.href = '/';
        }).catch(function(error) {
            // An error happened
            console.error("Error signing out:", error);
        });
    });

    // Toggle between login and register forms
    document.getElementById('show-register')?.addEventListener('click', function() {
        document.getElementById('login-container').style.display = 'none';
        document.getElementById('register-container').style.display = 'block';
    });
    
    document.getElementById('show-login')?.addEventListener('click', function() {
        document.getElementById('register-container').style.display = 'none';
        document.getElementById('login-container').style.display = 'block';
    });
});