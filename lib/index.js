import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const SKILL_NAMES = ["pdf-math"];

const name = "pdf-math";
const inject = ["skills"];

/**
 * 只解析本技能自己写的 frontmatter，避免依赖 yaml 包
 * （宿主用 pnpm 安装，未提升的依赖不一定能被解析到）。
 */
function parseSkill(raw, path) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/u.exec(raw);
  if (!m || m[1] === undefined) {
    throw new Error(`skill-pdf-math: ${path} has no YAML frontmatter`);
  }
  const descMatch = /^description:\s*(.+)$/mu.exec(m[1]);
  const description = descMatch
    ? descMatch[1].trim().replace(/^["']|["']$/g, "")
    : undefined;
  if (typeof description !== "string" || description.length === 0) {
    throw new Error(`skill-pdf-math: ${path} has no description`);
  }
  return { description, content: raw.slice(m[0].length).trim() };
}

/**
 * 技能正文保持"与机器无关"，本机真实路径在取用时拼在末尾。
 * 这样同一份包换台机器也能用（office 技能插件也是这个做法）。
 */
function localPaths(dir) {
  const ocr = join(homedir(), ".dsh", "runtimes", "formula-ocr", "py");
  const posix = (p) => p.split(/[\\/]/).join("/");
  return [
    "",
    "## 本机路径（由插件在运行时注入）",
    "",
    `- 本技能目录: \`${posix(dir)}\``,
    `- 工具: \`${posix(join(dir, "pdf_formula.py"))}\``,
    `- 公式 OCR 运行时（存在才可用）: \`${posix(ocr)}\``,
    "",
    "工具需要带 pymupdf 的解释器，先 `python -c \"import pymupdf\"` 验证再用。"
  ].join("\n");
}

function apply(ctx, config = {}) {
  // 注册过程必须"失败即降级"：技能坏了顶多没有技能，
  // 绝不能让宿主因为一个第三方插件起不来。
  try {
    const assetRoot =
      config.assetRoot ?? fileURLToPath(new URL("../assets/", import.meta.url));

    const candidates = SKILL_NAMES.map((skillName) => {
      const directory = join(assetRoot, skillName);
      const path = join(directory, "SKILL.md");
      const { description } = parseSkill(readFileSync(path, "utf8"), path);
      return {
        name: skillName,
        description,
        invocation: { modelInvocable: true, userInvocable: true },
        provider: "dsh-pdf-math",
        source: "bundled",
        rank: 120,
        resourceBase: {
          kind: "directory",
          path: directory.split(/[\\/]/).join("/")
        },
        locator: path
      };
    });

    const provider = {
      name: "dsh-pdf-math",
      list: () => Promise.resolve(candidates),
      async get(candidate, options) {
        const { rank: _rank, locator, ...summary } = candidate;
        const { content } = parseSkill(
          await readFile(locator, { encoding: "utf8", signal: options.signal }),
          locator
        );
        return {
          ...summary,
          content: content + localPaths(summary.resourceBase.path)
        };
      }
    };

    ctx.skills.registerProvider(() => provider);
  } catch (error) {
    console.warn("[skill-pdf-math] 注册失败，已跳过:", error?.message ?? error);
  }
}

export { apply, inject, name };
