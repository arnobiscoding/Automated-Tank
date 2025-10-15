import cv2
import numpy as np

# Open webcam (use 0 for default camera)
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # Get frame dimensions
    h, w, _ = frame.shape
    center_x, center_y = w // 2, h // 2

    # Convert BGR to HSV
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Define red color range in HSV
    lower_red1 = np.array([0, 170, 120])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 170, 120])
    upper_red2 = np.array([180, 255, 255])

    # Combine both masks
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = mask1 + mask2

    # Noise removal
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, np.ones((5,5), np.uint8))

    # Find contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    target_contour = None
    max_area = 0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 500:
            # Find the largest red object (main target)
            if area > max_area:
                max_area = area
                target_contour = contour

            # Draw all red objects (secondary targets)
            cv2.drawContours(frame, [contour], -1, (0, 255, 0), 2)

    # Draw the main target
    if target_contour is not None:
        x, y, w_box, h_box = cv2.boundingRect(target_contour)
        cv2.rectangle(frame, (x, y), (x + w_box, y + h_box), (255, 0, 0), 3)

        # Compute center of target
        M = cv2.moments(target_contour)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
            cv2.putText(frame, "Target", (cx - 30, cy - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            # Draw camera center
            cv2.circle(frame, (center_x, center_y), 6, (0, 255, 255), -1)
            cv2.line(frame, (center_x - 20, center_y), (center_x + 20, center_y), (0, 255, 255), 1)
            cv2.line(frame, (center_x, center_y - 20), (center_x, center_y + 20), (0, 255, 255), 1)

            # Determine direction feedback
            dx = cx - center_x
            dy = cy - center_y
            radius = 50  # tolerance for being “centered”

            direction = ""

            if abs(dx) < radius and abs(dy) < radius:
                direction = "Centered ✅"
            elif abs(dx) > abs(dy):
                if dx < 0:
                    direction = "⬅ Move LEFT"
                else:
                    direction = "➡ Move RIGHT"
            else:
                if dy < 0:
                    direction = "⬇ Move DOWN"
                else:
                    direction = "⬆ Move UP"

            cv2.putText(frame, direction, (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(frame, direction, (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 1, cv2.LINE_AA)

    # Show the video
    cv2.imshow("Red Object Tracking with Direction", frame)

    # Exit with 'q'
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
