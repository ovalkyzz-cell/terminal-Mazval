#!/usr/bin/env python3
"""
CONTOH FILE PYTHON UNTUK TESTING TERMINAL MAZVAL

File ini mencakup berbagai contoh untuk menguji:
1. Basic output dan syntax
2. Input user (python3 -c)
3. String manipulation
4. Math operations
5. Looping
6. Functions
7. List/Dictionary operations
8. File operations
9. Import modules
10. Exception handling
"""

import os
import sys
import time
import json

# ==================== 1. BASIC OUTPUT ====================
print("=" * 50)
print("TERMINAL MAZVAL - PYTHON TEST SUITE")
print("=" * 50)
print()

# ==================== 2. VARIABLES & DATA TYPES ====================
print("### DATA TYPES ###")
text = "Hello, Terminal Mazval!"
number = 42
pi = 3.14159
is_active = True

print(f"String: {text}")
print(f"Integer: {number}")
print(f"Float: {pi:.4f}")
print(f"Boolean: {is_active}")
print()

# ==================== 3. STRING MANIPULATION ====================
print("### STRING MANIPULATION ###")
text = "mazval-term"
print(f"Original: {text}")
print(f"Upper: {text.upper()}")
print(f"Lower: {text.lower()}")
print(f"Capitalized: {text.capitalize()}")
print(f"Length: {len(text)}")
print()

# ==================== 4. MATH OPERATIONS ====================
print("### MATH OPERATIONS ###")
a, b = 15, 7
print(f"a = {a}, b = {b}")
print(f"a + b = {a + b}")
print(f"a - b = {a - b}")
print(f"a * b = {a * b}")
print(f"a / b = {a / b:.2f}")
print(f"a // b = {a // b}")
print(f"a % b = {a % b}")
print(f"a ** b = {a ** b}")
print()

# ==================== 5. LOOPS ====================
print("### LOOPS ###")

# For loop
print("For loop (1-5):")
for i in range(1, 6):
    print(f"  {i} x {i} = {i * i}")

# While loop
print("\nWhile loop (countdown):")
count = 5
while count > 0:
    print(f"  {count}...")
    count -= 1
print("  Blastoff!")
print()

# ==================== 6. FUNCTIONS ====================
print("### FUNCTIONS ###")

def greet(name):
    """Fungsi sapaan dengan parameter"""
    return f"Hello, {name}!"

def calculate_sum(*args):
    """Fungsi dengan variadic arguments"""
    return sum(args)

def factorial(n):
    """Fungsi rekursif factorial"""
    if n <= 1:
        return 1
    return n * factorial(n - 1)

print(f"greet('User') = {greet('User')}")
print(f"calculate_sum(1, 2, 3, 4, 5) = {calculate_sum(1, 2, 3, 4, 5)}")
print(f"factorial(5) = {factorial(5)}")
print()

# ==================== 7. LISTS ====================
print("### LISTS ###")
fruits = ["apel", "pisang", "jeruk", "mangga"]
print(f"Original: {fruits}")

fruits.append("anggur")
print(f"After append: {fruits}")

print(f"First fruit: {fruits[0]}")
print(f"Last fruit: {fruits[-1]}")
print(f"Sliced (1:3): {fruits[1:3]}")
print(f"Length: {len(fruits)}")
print()

# ==================== 8. DICTIONARIES ====================
print("### DICTIONARIES ###")
student = {
    "name": "Budi",
    "age": 20,
    "grade": "A",
    "subjects": ["Matematika", "Fisika", "Kimia"]
}

print(f"Student: {json.dumps(student, indent=2)}")
print(f"Name: {student['name']}")
print(f"Subjects: {', '.join(student['subjects'])}")
print()

# ==================== 9. LIST COMPREHENSION ====================
print("### LIST COMPREHENSION ###")
squares = [x**2 for x in range(1, 11)]
evens = [x for x in range(1, 21) if x % 2 == 0]
print(f"Squares (1-10): {squares}")
print(f"Evens (1-20): {evens}")
print()

# ==================== 10. FILE OPERATIONS ====================
print("### FILE OPERATIONS ###")

# Write file
test_file = "/tmp/mazval_test.txt"
with open(test_file, "w") as f:
    f.write("Test file untuk Terminal Mazval\n")
    f.write("Created: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    f.write("Line 3: Python Testing\n")

print(f"File created: {test_file}")

# Read file
with open(test_file, "r") as f:
    content = f.read()
    print(f"File content:\n{content}")

# Cleanup
os.remove(test_file)
print("File deleted")
print()

# ==================== 11. EXCEPTION HANDLING ====================
print("### EXCEPTION HANDLING ###")

def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return "Error: Division by zero"
    except TypeError:
        return "Error: Invalid type"

print(f"divide(10, 2) = {divide(10, 2)}")
print(f"divide(10, 0) = {divide(10, 0)}")
print(f"divide(10, 'a') = {divide(10, 'a')}")
print()

# ==================== 12. OS & SYSTEM OPERATIONS ====================
print("### OS & SYSTEM OPERATIONS ###")
print(f"Python version: {sys.version.split()[0]}")
print(f"Working directory: {os.getcwd()}")
print(f"Home directory: {os.path.expanduser('~')}")
print(f"Environment PATH: {os.environ.get('PATH', 'N/A')[:60]}...")
print()

# ==================== 13. DATE & TIME ====================
print("### DATE & TIME ###")
now = time.localtime()
print(f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S', now)}")
print(f"Date format (ID): {time.strftime('%d/%m/%Y', now)}")
print()

# ==================== 14. IMPORT MODULES ====================
print("### IMPORT MODULES ###")
print(f"os module: {os.__file__}")
print(f"sys module: {sys.__file__}")
print()

# ==================== 15. FINAL MESSAGE ====================
print("=" * 50)
print("SEMUA TEST SELESAI!")
print("=" * 50)
print()
print("Untuk menjalankan file ini di Terminal Mazval:")
print("1. Upload file ini ke Storage")
print("2. Jalankan dengan: python3 nama_file.py")
print("   atau: run nama_file.py")
print()
print("Selamat mencoba Terminal Mazval! 🚀")
