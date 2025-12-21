import type { DocumentAnalysis, Group } from '../types/DocumentAnalysis';
import './ReviewScreen.css';

interface ReviewScreenProps {
  analysis: DocumentAnalysis;
  allGroups: Group[];  // Includes both original and user-created groups
  currentGroupId: string | null;
  onChangeGroupId: (groupId: string) => void;
}

export function ReviewScreen({
  analysis,
  allGroups,
  currentGroupId,
  onChangeGroupId,
}: ReviewScreenProps) {
  // Derive current group from currentGroupId
  const currentGroup = allGroups.find((g) => g.groupId === currentGroupId);

  return (
    <div className="review-screen">
      <div className="review-content">
        {/* Current Group Display */}
        <div className="current-group-section">
          {currentGroup ? (
            <h2>Current Group: <span className="group-name-highlight">{currentGroup.label}</span> <span className="group-id-text">(Group ID: {currentGroup.groupId})</span></h2>
          ) : (
            <h2>Current Group: <span className="no-group">No group selected</span></h2>
          )}
        </div>

        {/* All Groups List */}
        <div className="groups-list-section">
          <h2>All Groups ({allGroups.length})</h2>
          <ul className="groups-list">
            {allGroups.map((group) => (
              <li
                key={group.groupId}
                className={`group-item ${group.groupId === currentGroupId ? 'active' : ''} ${group.kind === 'user-created' ? 'user-created' : ''}`}
                onClick={() => onChangeGroupId(group.groupId)}
              >
                <div className="group-item-content">
                  <span className="group-label">
                    {group.label}
                    {group.kind === 'user-created' && <span className="user-badge">★</span>}
                  </span>
                  <span className="group-meta">
                    {group.occurrences.length} occurrences
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
