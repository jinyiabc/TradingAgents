/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Build a fully static bundle for Azure Static Web Apps Free tier.
  // All routing happens via query strings (see web/app/jobs/page.tsx); no
  // dynamic route segments — so no managed Functions runtime is needed.
  output: "export",
  // SWA serves index.html for paths without trailing slashes by default;
  // emitting trailing-slash variants keeps deep links working out of the box.
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
