import type { NextConfig } from "next";

// "standalone" is what the Docker image runs: one folder, with no node_modules to install.
const config: NextConfig = {
  output: "standalone",
  devIndicators: false,
};

export default config;
