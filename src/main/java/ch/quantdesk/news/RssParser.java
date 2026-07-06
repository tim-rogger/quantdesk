package ch.quantdesk.news;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

public final class RssParser {

    private RssParser() {
    }

    public static List<NewsItem> parse(String symbol, String rssXml) {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            factory.setExpandEntityReferences(false);
            DocumentBuilder builder = factory.newDocumentBuilder();
            Document document = builder.parse(
                    new ByteArrayInputStream(rssXml.getBytes(StandardCharsets.UTF_8)));
            NodeList itemNodes = document.getElementsByTagName("item");
            List<NewsItem> items = new ArrayList<>();
            for (int index = 0; index < itemNodes.getLength(); index++) {
                Element item = (Element) itemNodes.item(index);
                String headline = textOf(item, "title");
                String url = textOf(item, "link");
                Instant publishedAt = parsePubDate(textOf(item, "pubDate"));
                items.add(new NewsItem(symbol, headline, publishedAt, url));
            }
            return items;
        } catch (Exception e) {
            return List.of();
        }
    }

    private static String textOf(Element item, String tag) {
        NodeList nodes = item.getElementsByTagName(tag);
        if (nodes.getLength() == 0) {
            return "";
        }
        return nodes.item(0).getTextContent().trim();
    }

    private static Instant parsePubDate(String value) {
        try {
            return ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
        } catch (Exception e) {
            return Instant.now();
        }
    }
}
