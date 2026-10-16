// PostCSS runs Tailwind CSS v4 over app/globals.css during `next build` and `next dev`.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
