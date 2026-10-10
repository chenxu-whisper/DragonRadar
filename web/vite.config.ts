import { defineConfig } from 'vite';

const target = process.env.DRAGON_API_PROXY || 'http://127.0.0.1:8020';

export default defineConfig({
  cacheDir: '.temp/vite',
  optimizeDeps: {
    include: ['react', 'react-dom/client', 'react/jsx-runtime', 'lucide-react', 'decimal.js', 'echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target }, '/health': { target } },
  },
  build: {
    outDir: 'dist',
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'chart-renderer', test: /node_modules[\\/]zrender[\\/]/, priority: 20 },
            { name: 'chart-core', test: /node_modules[\\/]echarts[\\/]lib[\\/](core|model|view|data|util)[\\/]/, priority: 10 },
            { name: 'chart-components', test: /node_modules[\\/]echarts[\\/]/ },
          ],
        },
      },
    },
  },
});
