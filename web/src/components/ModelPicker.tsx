import { useEffect, useState } from "react";
import { api } from "../api";
import { storage } from "../storage";
import type { ModelInfo } from "../types";

export function ModelPicker() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [current, setCurrent] = useState(storage.getModel());

  useEffect(() => {
    api
      .get<ModelInfo[]>("/api/v1/models")
      .then((list) => setModels(Array.isArray(list) ? list : []))
      .catch(() => {
        // 拉取失败时退回默认模型，不影响主流程
      });
  }, []);

  function change(name: string) {
    setCurrent(name);
    storage.setModel(name);
  }

  if (models.length === 0) {
    return (
      <div className="model-picker">
        <select value={current} onChange={(e) => change(e.target.value)}>
          <option value={current}>{current}</option>
        </select>
        <span className="arrow">▾</span>
      </div>
    );
  }

  return (
    <div className="model-picker">
      <select value={current} onChange={(e) => change(e.target.value)}>
        {models.map((m) => (
          <option key={m.name} value={m.name}>
            {m.display_name || m.name}
          </option>
        ))}
      </select>
      <span className="arrow">▾</span>
    </div>
  );
}
