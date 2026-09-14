export default function adapter(options = {}) {
  const pages = options.pages || 'build';
  const assets = options.assets || pages;
  const fallback = options.fallback || 'index.html';

  return {
    name: 'tametools-static-adapter',
    async adapt(builder) {
      builder.rimraf(assets);
      builder.rimraf(pages);
      builder.mkdirp(assets);
      builder.mkdirp(pages);
      builder.writeClient(assets);
      builder.writePrerendered(pages);
      if (fallback) {
        await builder.generateFallback(`${pages}/${fallback}`);
      }
    }
  };
}
