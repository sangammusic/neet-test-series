import os
from flask import Flask, render_template_string
from supabase import create_client, Client

app = Flask(__name__)

# Tumhari exact Supabase details
SUPABASE_URL = "https://hssgvmjhtquvllc.supabase.co"
SUPABASE_KEY = "sb_publishable_lXcwSsZmkdel-BlatIuRrw_ZHJEfW38"

# Supabase database connection initialize karna
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Premium Website Ka Design (HTML + CSS + Javascript)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEET Premium Test Series</title>
    <!-- Tailwind CSS for Modern Premium UI -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- MathJax for Physics/Chemistry Symbols (Roots, Alpha, Beta) -->
    <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
    <!-- SweetAlert2 for Premium Telegram Popup -->
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
</head>
<body class="bg-slate-50 text-gray-800 font-sans">

    <!-- Top Navigation Bar -->
    <nav class="bg-white p-4 shadow-md flex justify-between items-center border-b-4 border-indigo-600">
        <div class="text-2xl font-black text-indigo-700 tracking-wider">NEET<span class="text-orange-500">MOCK</span></div>
        <div>
            <button class="bg-indigo-600 text-white px-6 py-2 rounded-lg font-bold shadow hover:bg-indigo-700 transition">Login / Register</button>
        </div>
    </nav>

    <!-- Main Container -->
    <div class="container mx-auto mt-12 p-8 bg-white rounded-2xl shadow-xl max-w-3xl text-center border border-gray-100">
        <h1 class="text-4xl font-extrabold mb-4 text-gray-900">Real NTA Exam Experience</h1>
        <p class="text-gray-500 mb-8 text-lg">Attempt Premium Mock Tests for Physics, Chemistry, and Biology.</p>
        
        <!-- Physics Symbols Demo Section -->
        <div class="bg-indigo-50 p-6 rounded-xl border border-indigo-100 text-left mb-8 shadow-inner">
            <h3 class="font-bold text-xl text-indigo-900 mb-3">✅ Math & Physics Support Active:</h3>
            <p class="text-lg mb-2">Equation: \\( x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a} \\)</p>
            <p class="text-lg">Symbols: \\( \\alpha, \\beta, \\gamma, \\theta, \\mu_0, \\int_{a}^{b} \\)</p>
        </div>

        <button class="bg-orange-500 text-white px-8 py-3 rounded-xl font-bold text-lg shadow-lg hover:bg-orange-600 transition w-full mb-6">START FREE TEST</button>

        <!-- Support Section -->
        <div class="mt-8 pt-6 border-t border-gray-200 text-sm text-gray-500">
            Having trouble? Contact Support on Telegram: 
            <a href="https://t.me/s_u_k_oo_n_143" target="_blank" class="text-indigo-600 font-bold hover:underline">@s_u_k_oo_n_143</a>
        </div>
    </div>

    <!-- Telegram Popup Logic -->
    <script>
        window.onload = function() {
            setTimeout(() => {
                Swal.fire({
                    title: '🚀 Join Our Community!',
                    text: 'Get daily free NEET test updates, PDF notes, and PYQs.',
                    imageUrl: 'https://upload.wikimedia.org/wikipedia/commons/8/82/Telegram_logo.svg',
                    imageWidth: 80,
                    imageHeight: 80,
                    showCancelButton: true,
                    confirmButtonColor: '#2AABEE',
                    cancelButtonColor: '#d33',
                    confirmButtonText: 'Join @neetjeesangam',
                    cancelButtonText: 'Maybe Later',
                    customClass: {
                        popup: 'rounded-2xl'
                    }
                }).then((result) => {
                    if (result.isConfirmed) {
                        window.open('https://t.me/neetjeesangam', '_blank');
                    }
                })
            }, 1000); // 1 second baad popup aayega
        };
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(debug=True)
