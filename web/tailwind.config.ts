/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter Variable', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        surface: {
          base: '#060a14',
          card: 'rgba(255,255,255,0.04)',
          elevated: 'rgba(255,255,255,0.06)',
        },
        macro: {
          protein: '#2dd4bf',
          carbs: '#f97316',
          fat: '#22c55e',
          calories: '#3b82f6',
        },
        accent: {
          DEFAULT: '#10b981',
          dim: 'rgba(16,185,129,0.15)',
        },
        streak: {
          DEFAULT: '#f97316',
          glow: 'rgba(249,115,22,0.3)',
        },
        glass: {
          border: 'rgba(255,255,255,0.08)',
        },
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0, 0, 0, 0.3)',
        'glass-sm': '0 4px 16px rgba(0, 0, 0, 0.2)',
        'glass-lg': '0 16px 48px rgba(0, 0, 0, 0.4)',
        glow: '0 0 20px rgba(16, 185, 129, 0.3)',
        'glow-blue': '0 0 20px rgba(59, 130, 246, 0.3)',
        'glow-streak': '0 0 20px rgba(249, 115, 22, 0.3)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        glow: 'glow 2s ease-in-out infinite alternate',
        'slide-down': 'slide-down 0.3s ease-out',
        'fade-in': 'fade-in 0.2s ease-out',
        shimmer: 'shimmer 1.5s ease-in-out infinite',
        'slide-in-left': 'slide-in-left 0.35s ease-out',
        'slide-in-right': 'slide-in-right 0.35s ease-out',
        'shake-x': 'shake-x 0.4s ease-in-out',
        'slide-up': 'slide-up 0.2s ease-out',
        wiggle: 'wiggle 0.25s ease-in-out infinite',
        'flame-flicker': 'flame-flicker 2s ease-in-out infinite',
        'shield-pulse': 'shield-pulse 1.6s ease-in-out infinite',
        'shield-bash': 'shield-bash 0.7s ease-out',
      },
      keyframes: {
        glow: {
          '0%': { opacity: '0.5' },
          '100%': { opacity: '1' },
        },
        'slide-down': {
          '0%': { opacity: '0', transform: 'translateY(-12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'slide-in-left': {
          '0%': { opacity: '0', transform: 'translateX(-60px) scale(0.95)' },
          '60%': { opacity: '1', transform: 'translateX(4px) scale(1)' },
          '100%': { opacity: '1', transform: 'translateX(0) scale(1)' },
        },
        'slide-in-right': {
          '0%': { opacity: '0', transform: 'translateX(60px) scale(0.95)' },
          '60%': { opacity: '1', transform: 'translateX(-4px) scale(1)' },
          '100%': { opacity: '1', transform: 'translateX(0) scale(1)' },
        },
        'shake-x': {
          '0%': { transform: 'translateX(0)' },
          '20%': { transform: 'translateX(-8px)' },
          '40%': { transform: 'translateX(6px)' },
          '60%': { transform: 'translateX(-4px)' },
          '80%': { transform: 'translateX(2px)' },
          '100%': { transform: 'translateX(0)' },
        },
        'slide-up': {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        wiggle: {
          '0%, 100%': { transform: 'rotate(-1.5deg)' },
          '50%': { transform: 'rotate(1.5deg)' },
        },
        'flame-flicker': {
          '0%, 100%': { transform: 'scale(1)', opacity: '1' },
          '25%': { transform: 'scale(1.12) rotate(-3deg)', opacity: '0.9' },
          '50%': { transform: 'scale(0.95) rotate(2deg)', opacity: '1' },
          '75%': { transform: 'scale(1.08) rotate(-1deg)', opacity: '0.85' },
        },
        'shield-pulse': {
          '0%, 100%': { transform: 'scale(1)', filter: 'drop-shadow(0 0 12px rgba(59,130,246,0.6))' },
          '50%': { transform: 'scale(1.06)', filter: 'drop-shadow(0 0 24px rgba(59,130,246,0.9))' },
        },
        'shield-bash': {
          '0%': { transform: 'scale(0.4) rotate(-12deg)', opacity: '0' },
          '40%': { transform: 'scale(1.2) rotate(8deg)', opacity: '1' },
          '70%': { transform: 'scale(0.96) rotate(-3deg)', opacity: '1' },
          '100%': { transform: 'scale(1) rotate(0deg)', opacity: '1' },
        },
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
