import { NodeViewWrapper, NodeViewContent, type NodeViewProps } from '@tiptap/react';
import styles from './ComposeCodeBlock.module.css';

// Matches lowlight's `common` grammar bundle (createLowlight(common).listLanguages()) —
// keep in sync if that bundle in ComposeView.tsx's CodeBlockLowlight.configure() ever changes.
const LANGUAGES = [
  'plaintext', 'bash', 'c', 'cpp', 'csharp', 'css', 'diff', 'go', 'graphql', 'ini', 'java',
  'javascript', 'json', 'kotlin', 'less', 'lua', 'makefile', 'markdown', 'objectivec', 'perl',
  'php', 'python', 'r', 'ruby', 'rust', 'scss', 'shell', 'sql', 'swift', 'typescript', 'xml',
  'yaml',
];

/** A language picker in the corner of each code block — lowlight already
 * re-highlights automatically whenever the node's `language` attribute
 * changes, so this only needs to write that attribute. */
export default function ComposeCodeBlock({ node, updateAttributes, editor }: NodeViewProps) {
  const language = (node.attrs.language as string | null) ?? 'plaintext';

  return (
    <NodeViewWrapper className={styles.wrap}>
      <select
        className={styles.picker}
        contentEditable={false}
        value={LANGUAGES.includes(language) ? language : 'plaintext'}
        disabled={!editor.isEditable}
        onChange={(e) => updateAttributes({ language: e.target.value })}
      >
        {LANGUAGES.map((lang) => (
          <option key={lang} value={lang}>{lang}</option>
        ))}
      </select>
      <pre>
        <NodeViewContent<'code'> as="code" />
      </pre>
    </NodeViewWrapper>
  );
}
