const rootDir = new URL('./', import.meta.url).pathname

export default {
  resolve: {
    alias: {
      '~': rootDir,
      '@': rootDir
    }
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.spec.ts']
  }
}
