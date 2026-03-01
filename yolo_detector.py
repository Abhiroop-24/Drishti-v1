"""
DRISHTI - YOLOv8 People Detection Module
Detects people, estimates distance, and provides alerts.
"""

import cv2
import numpy as np
import logging
from ultralytics import YOLO
from config import YOLOConfig

logger = logging.getLogger("drishti.yolo")


class PersonDetection:
    """Represents a single detected person."""
    
    def __init__(self, bbox, confidence, distance, position):
        self.bbox = bbox          # (x1, y1, x2, y2)
        self.confidence = confidence
        self.distance = distance  # estimated distance in meters
        self.position = position  # "left", "center", "right"
        self.width = bbox[2] - bbox[0]
        self.height = bbox[3] - bbox[1]
    
    def __repr__(self):
        return (f"Person(dist={self.distance:.1f}m, "
                f"pos={self.position}, conf={self.confidence:.2f})")
    
    @property
    def proximity_level(self):
        """Returns proximity category."""
        if self.distance < 1.5:
            return "very_close"
        elif self.distance < 3.0:
            return "close"
        elif self.distance < 6.0:
            return "medium"
        else:
            return "far"


class YOLODetector:
    """YOLOv8-based people detector with distance estimation."""
    
    def __init__(self):
        logger.info(f"Loading YOLO model: {YOLOConfig.MODEL}")
        self.model = YOLO(YOLOConfig.MODEL)
        self.confidence = YOLOConfig.CONFIDENCE
        self.max_people = YOLOConfig.MAX_PEOPLE
        self.focal_length = YOLOConfig.FOCAL_LENGTH
        self.real_height = YOLOConfig.REAL_PERSON_HEIGHT
        self.alerts_enabled = False
        logger.info("YOLO model loaded successfully")
    
    def detect(self, frame):
        """
        Detect people in frame.
        
        Args:
            frame: BGR image (numpy array)
            
        Returns:
            list of PersonDetection objects (max self.max_people)
        """
        results = self.model(frame, verbose=False, conf=self.confidence)
        detections = []
        
        for result in results:
            if result.boxes is None:
                continue
                
            for box in result.boxes:
                cls_id = int(box.cls[0])
                
                # Only detect persons (class 0 in COCO)
                if cls_id != YOLOConfig.PERSON_CLASS_ID:
                    continue
                
                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                bbox = (int(x1), int(y1), int(x2), int(y2))
                
                # Estimate distance
                person_height_px = y2 - y1
                distance = self._estimate_distance(person_height_px)
                
                # Determine position
                frame_w = frame.shape[1]
                center_x = (x1 + x2) / 2
                position = self._get_position(center_x, frame_w)
                
                detections.append(
                    PersonDetection(bbox, conf, distance, position)
                )
        
        # Sort by distance (closest first), limit to max_people
        detections.sort(key=lambda d: d.distance)
        detections = detections[:self.max_people]
        
        return detections
    
    def _estimate_distance(self, height_pixels):
        """
        Estimate distance using pinhole camera model.
        D = (H_real * f) / H_pixels
        """
        if height_pixels <= 0:
            return float('inf')
        distance = (self.real_height * self.focal_length) / height_pixels
        return round(distance, 1)
    
    def _get_position(self, center_x, frame_width):
        """Determine if person is left, center, or right."""
        third = frame_width / 3
        if center_x < third:
            return "left"
        elif center_x < 2 * third:
            return "center"
        else:
            return "right"
    
    def generate_alert_message(self, detections):
        """
        Generate spoken alert message for detected people.
        Cooldown is managed by the caller (DrishtiApp); this
        method just checks proximity and builds the message.

        Returns:
            str or None: Alert message, or None if no alert needed.
        """
        if not self.alerts_enabled or not detections:
            return None

        # Check for very close people (< 1.5m) - urgent alert
        very_close = [d for d in detections if d.proximity_level == "very_close"]
        close = [d for d in detections if d.proximity_level == "close"]
        
        parts = []
        
        if very_close:
            if len(very_close) == 1:
                p = very_close[0]
                parts.append(
                    f"Warning! Person very close, {p.distance} meters "
                    f"to your {p.position}"
                )
            else:
                parts.append(
                    f"Warning! {len(very_close)} people very close to you"
                )
                for p in very_close:
                    parts.append(f"{p.distance} meters to your {p.position}")
        
        if close:
            if len(close) == 1:
                p = close[0]
                parts.append(
                    f"Person nearby at {p.distance} meters, "
                    f"to your {p.position}"
                )
            else:
                parts.append(f"{len(close)} people nearby")
        
        total = len(detections)
        if total > len(very_close) + len(close):
            remaining = total - len(very_close) - len(close)
            noun = "person" if remaining == 1 else "people"
            parts.append(f"{remaining} more {noun} detected further away")
        
        if parts:
            return ". ".join(parts) + "."
        return None
    
    def draw_detections(self, frame, detections):
        """
        Draw bounding boxes and info on frame for visualization.
        
        Args:
            frame: BGR image
            detections: list of PersonDetection
            
        Returns:
            Annotated frame
        """
        annotated = frame.copy()
        
        for i, det in enumerate(detections):
            x1, y1, x2, y2 = det.bbox
            
            # Color based on distance
            if det.proximity_level == "very_close":
                color = (0, 0, 255)     # Red
            elif det.proximity_level == "close":
                color = (0, 165, 255)   # Orange
            elif det.proximity_level == "medium":
                color = (0, 255, 255)   # Yellow
            else:
                color = (0, 255, 0)     # Green
            
            # Draw bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            
            # Draw label
            label = f"P{i+1}: {det.distance}m ({det.position})"
            label_size, _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
            )
            
            # Background for label
            cv2.rectangle(
                annotated,
                (x1, y1 - label_size[1] - 10),
                (x1 + label_size[0], y1),
                color, -1
            )
            cv2.putText(
                annotated, label,
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2
            )
        
        # Draw status bar
        status = f"People: {len(detections)}/{self.max_people}"
        if self.alerts_enabled:
            status += " | ALERTS: ON"
        else:
            status += " | ALERTS: OFF"
        
        cv2.rectangle(annotated, (0, 0), (350, 35), (0, 0, 0), -1)
        cv2.putText(
            annotated, status,
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7,
            (0, 255, 0) if self.alerts_enabled else (100, 100, 100), 2
        )
        
        return annotated
    
    def toggle_alerts(self):
        """Toggle YOLO alerts on/off."""
        self.alerts_enabled = not self.alerts_enabled
        state = "enabled" if self.alerts_enabled else "disabled"
        logger.info(f"YOLO alerts {state}")
        return self.alerts_enabled

    def describe_all_people(self, detections):
        """
        Build a spoken summary of ALL detected people and their distances.
        Used for B3 periodic dictation — not just urgent close-people alerts.

        Returns:
            str or None: Spoken description, or None if no one detected.
        """
        if not detections:
            return None

        n = len(detections)
        noun = "person" if n == 1 else "people"
        parts = [f"{n} {noun} detected."]

        for i, d in enumerate(detections, start=1):
            dist_str = f"{d.distance} meter{'s' if d.distance != 1.0 else ''}"
            urgency = ""
            if d.proximity_level == "very_close":
                urgency = " Warning, very close!"
            elif d.proximity_level == "close":
                urgency = " Nearby."
            parts.append(
                f"Person {i}: {dist_str} to your {d.position}.{urgency}"
            )

        return " ".join(parts)
    
    def get_summary(self, detections):
        """Get a text summary of current detections."""
        if not detections:
            return "No people detected."
        
        lines = [f"{len(detections)} {'person' if len(detections) == 1 else 'people'} detected:"]
        for i, det in enumerate(detections):
            lines.append(
                f"  Person {i+1}: {det.distance}m away, "
                f"to your {det.position} ({det.proximity_level})"
            )
        return "\n".join(lines)
