import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import {ConfigProvider, theme} from "antd";
import zhCN from "antd/locale/zh_CN";

import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: "#27d7c2",
          colorInfo: "#4f8cff",
          colorSuccess: "#49d17d",
          colorWarning: "#f7b955",
          colorError: "#ff6174",
          borderRadius: 12,
          fontFamily:
            '"Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
        }
      }}
    >
      <App />
    </ConfigProvider>
  </StrictMode>
);

